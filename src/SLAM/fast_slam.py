#! venv/bin/python
# -*- encoding:utf-8 -*-

'''
The goal of slam is to get an accuracy state of robot.
And build the map according to the accuracy state.
For fast slam, replace the Robot state estimation method with particle filter
the mainly steps are as follows:
1. random particles around the initial state.
2. move-motion:
    for each particle, do state predict according to the move instruction and kinematic model.
    use the mean of particles as the robot's prediction state.

3. observation:
    for each particle, use extend-kalman filter calc the covariance of each landmark, and the estimates the state.
    Then, we can get mean of particles as the robot's estimation state.
4. resample
    according to the particles'weight, do resample.
    The resample process just replaces invalid particles.
'''

import numpy as np
import matplotlib.pyplot as plt
import math
import random


def normalize(angle):
    while angle > math.pi:
        angle -= 2 * math.pi
    while angle < -math.pi:
        angle += math.pi * 2
    return angle


class Room(object):
    def __init__(self):
        self.landmarks = []

    def add_landmark(self, x, y):
        self.landmarks.append([x, y])

    def get_landmarks_as_matrix(self):
        return np.matrix(self.landmarks).T

    def get_landmarks(self):
        return self.landmarks

    def get_number_of_landmarks(self):
        return len(self.landmarks)


class Car(object):
    OBSERVE_DISTANCE = 20
    """
    The real car, moves in room
    suppose the car moves with fixed speed.
    model:
    x = x + v * cos(yaw) * dt
    y = y + v * sin(yaw) * dt
    yaw = yaw + delta * dt
    
    control vector:
    [v, delta]
    """
    def __init__(self, x=0, y=0, yaw=0):
        self.x = x
        self.y = y
        self.yaw = yaw
        self.v = 0
        self.delta = 0
        self.process_variance = np.matrix(np.diag([0.1, np.radians(1)])) ** 2
        self.observe_variance = np.matrix(np.diag([0.1, np.radians(1)])) ** 2

    def move(self, v=1, delta=np.radians(5), dt=0.1):
        self.x = self.x + v * dt * math.cos(self.yaw)
        self.y = self.y + v * dt * math.sin(self.yaw)
        self.yaw = self.yaw + delta * dt
        self.v = v + np.random.randn() * self.process_variance[0, 0]
        self.delta = delta + np.random.randn() * self.process_variance[1, 1]

    def get_input(self):
        """
        [a, delta].T
        :return:
        """
        return np.matrix([self.v, self.delta]).T

    def get_state(self):
        """
        [x, y, yaw].T
        :return:
        """
        return np.matrix([self.x, self.y, self.yaw]).T

    def observe(self, room):
        """
        suppose the car is equipped with radar.
        :param room:
        :type room: Room
        :return: [[d, theta, label], ...]
        """
        observed = []
        i = 0
        for lx, ly in room.landmarks:
            dx = lx - self.x
            dy = ly - self.y
            d = math.sqrt(dx * dx + dy * dy)
            theta = math.atan2(dy, dx)
            if d < self.OBSERVE_DISTANCE:
                d = d + np.random.randn() * self.observe_variance[0, 0]
                theta = theta + np.random.randn() * self.observe_variance[1, 1]
                observed.append([d, theta, i])
            i += 1

        return np.matrix(observed)


class Particle:

    def __init__(self, initial_X, landmark_size, landmark_number, weight, copy=False):
        """
        :param initial_X:
        :type initial_X: [x, y, yaw, v]
        X = [x, y, theta, v, l1_x, l1_y, l2_x, l2_y, ...].T
        """
        if not copy:
            self.RS = len(initial_X)
            self.LS = landmark_size
            self.n_landmark = landmark_number
            self.X = np.zeros((self.RS + self.LS * self.n_landmark, 1))
            self.X[0: self.RS, :] = np.matrix(initial_X).T
            self.LP = np.matrix(np.zeros((landmark_number * self.LS, self.LS)))
            self.M_DIST_H = 2.0
            self.weight = weight

    def state(self):
        return self.X[: self.RS, :]

    def make_copy(self):
        o = Particle(self.X, self.LS, self.n_landmark, self.weight, copy=True)
        o.RS = self.RS
        o.LS = self.LS
        o.n_landmark = self.n_landmark
        o.X = self.X.copy()
        o.LP = self.LP.copy()
        o.weight = self.weight
        return o

    def _get_nth_landmark_state(self, n):
        start_pos = self.RS + n * self.LS
        end_pos = start_pos + self.LS
        return self.X[start_pos: end_pos, :]

    def _get_nth_landmark_covariance(self, n):
        start_pos = n * self.LS
        end_pos = start_pos + self.LS
        return self.LP[start_pos: end_pos, :]

    def _move_motion(self, u, dt):
        """

        :param u: [a, delta].T
        :param Q: np.matrix(np.diag([std_a, std_delta]))
        :param dt:
        :return:
        """
        F = np.matrix([[1.0, 0.0, 0],
                       [0.0, 1.0, 0],
                       [0.0, 0.0, 1.0],
                       ])
        B = np.matrix([[dt * math.cos(self.X[2, 0]), 0.0],
                       [dt * math.sin(self.X[2, 0]), 0.0],
                       [0.0, dt]])
        self.X[0:self.RS, :] = F * self.X[0:self.RS, :] + B * u
        self.X[2, 0] = normalize(self.X[2, 0])

    def predict(self, u, Q, dt):
        """
        for particles filter, here, we don't need to calculate the covariance.
        :param u:
        :param Q:
        :param dt:
        :return:
        """
        u_p = u + Q * np.matrix(np.random.randn(Q.shape[0])).T
        self._move_motion(u_p, dt)
        return self.X[0:self.RS, :]

    def update(self, z, R):
        """

        :param z: [[d_1, theta_1],
                    ...,
                    [d_i, theta_i],
                    ...,
                    [d_n, theta_n]]
        :type z: np.matrix
        :return:
        """
        self.weight = 1
        observed_n = z.shape[0]
        for i in range(observed_n):
            self.update_one_landmark(z[i, :].T, R)

        return self.X[0:self.RS, :], self.weight

    def update_one_landmark(self, z, R):
        """
        1. search matching landmark
        2.
        :param z: [d, theta].T
        :param R:
        :return:
        """
        lm_id = int(z[2, 0])
        if abs(self.X[lm_id * self.LS + self.RS, 0]) < 0.01:
            self._add_landmark(z, R)
        else:
            self._compute_weight(z)

            residual, Hx, Hlm, Slm = self._calc_innovation(lm_id, z, R)
            K = self._get_nth_landmark_covariance(lm_id) * Hlm.T * np.linalg.inv(Slm)

            start_pos = lm_id * self.LS
            end_pos = start_pos + self.LS
            self.X[self.RS+start_pos: self.RS+end_pos, :] = self.X[self.RS+start_pos: self.RS+end_pos, :] + K * residual
            P = self.LP[start_pos: end_pos, :]
            self.LP[start_pos: end_pos, :] = P - K * Hlm * P

    def _compute_weight(self, z):
        """
        measure the probability of residual between observed and calculated
        :param z:
        :return:
        """
        lm_id = int(z[2, 0])
        X_L = [self.X[0, 0] + z[0, 0] * math.cos(z[1, 0]), self.X[1, 0] + z[0, 0] * math.sin(z[1, 0])]
        X_L = np.matrix(X_L).T
        residual = X_L - self._get_nth_landmark_state(lm_id)
        P = self._get_nth_landmark_covariance(lm_id)
        try:
            invP = np.linalg.inv(P)
        except np.linalg.linalg.LinAlgError:
            return
        num = math.exp(-0.5 * residual.T * invP * residual)
        den = 2.0 * math.pi * math.sqrt(np.linalg.det(P))
        self.weight = self.weight * num / den

    def _add_landmark(self, z, R):
        """
        :param z:
        :param R:
        :return:
        """
        X_L = [self.X[0, 0] + z[0, 0] * math.cos(z[1, 0]), self.X[1, 0] + z[0, 0] * math.sin(z[1, 0])]
        X_L = np.matrix(X_L).T
        start_pos = int(z[2, 0]) * self.LS + self.RS
        end_pos = start_pos + self.LS
        self.X[start_pos: end_pos] = X_L
        # X_LM = H(R, z)
        # so, P = G_R * P_R_R * G_R.T + G_z * R * G_z.T
        G_r = np.matrix([[math.cos(z[1,0]), -z[0,0] * math.sin(z[1, 0])],
                         [math.sin(z[1, 0]), z[0, 0] * math.cos(z[1, 0])]])
        P = G_r * R * G_r.T
        start_pos = int(z[2, 0]) * self.LS
        end_pos = start_pos + self.LS
        self.LP[start_pos: end_pos, :] = P

    def _calc_innovation(self, n, z_observed, R):
        lm_real = self._get_nth_landmark_state(n)
        dx = lm_real[0, 0] - self.X[0, 0]
        dy = lm_real[1, 0] - self.X[1, 0]
        square_distance = dx * dx + dy * dy
        z = np.matrix([math.sqrt(square_distance), math.atan2(dy, dx)]).T
        residual = z_observed[:2, :] - z
        residual[1, 0] = normalize(residual[1, 0])

        Hx, Hlm = self._observation_jacob(square_distance, z[0, 0], dx, dy)
        Slm = Hlm * self._get_nth_landmark_covariance(n) * Hlm.T + R
        return residual, Hx, Hlm, Slm

    def _observation_jacob(self, square_distance, distance, dx, dy):
        Hx = np.matrix([[-dx / distance, -dy / distance, 0, 0],
                       [dy / square_distance, -dx / square_distance, 0, 0]])
        Hlm = np.matrix([[dx / distance, dy / distance],
                         [-dy / square_distance, dx / square_distance]])
        return Hx, Hlm


def normalize_particles(particles):
    ws = 0
    for p in particles:
        ws += p.weight
    try:
        ws = 1. / ws
        for p in particles:
            p.weight *= ws
    except ZeroDivisionError:
        for p in particles:
            p.weight = 1. / len(particles)


def estimate_pose(particles):
    X = np.zeros((3, 1))
    for p in particles:
        X += p.weight * p.state()
    return X


def resample(particles):
    """
    :param particles:
    :return:
    """
    normalize_particles(particles)
    weights = []
    for particle in particles:
        weights.append(particle.weight)
    weights = np.matrix(weights)
    Neff = 1. / ((weights * weights.T)[0,0] + 1e-30)
    if Neff < len(particles) / 2:
        wcum = np.cumsum(weights)
        wcum[0, -1] = 1

        resample_id = []
        for i in range(len(particles)):
            resample_id.append(random.uniform(0, 1.0))
        indexes = []
        for i in range(len(particles)):
            ind = 0
            while wcum[0, ind] < resample_id[i]:
                ind += 1
            indexes.append(ind)
        new_particles = []
        for i, ind in enumerate(indexes):
            new_particles.append(particles[ind].make_copy())
        particles = new_particles
        normalize_particles(particles)
    return particles


def predict(particles, u, Q, dt=0.1):
    """
    each particle does predict, and return the predict state.
    :param particles:
    :param u:
    :param Q:
    :param dt:
    :return:
    """
    RS = particles[0].RS
    X_pred = np.zeros((RS, 1))
    for particle in particles:
        X = particle.predict(u, Q, dt)
        X_pred = X_pred + X * particle.weight
    return X_pred


def update(particles, z, R):
    """
    for each particle does update
    :param particles:
    :param z:
    :param R:
    :return:
    """
    for i, particle in enumerate(particles):
        particle.update(z, R)
    particles = resample(particles)
    return estimate_pose(particles), particles


def main():
    room = Room()
    room.add_landmark(10.0, -2.0)
    room.add_landmark(15.0, 10.0)
    room.add_landmark(15.0, 15.0)
    room.add_landmark(10.0, 20.0)
    room.add_landmark(3.0, 15.0)
    room.add_landmark(-5.0, 20.0)
    room.add_landmark(-5.0, 5.0)
    room.add_landmark(-10.0, 15.0)

    car = Car()
    initial_X = [0, 0, 0]
    landmark_size = 2
    p_n = 50
    weight = 1. / p_n
    particles = [Particle(initial_X, landmark_size, room.get_number_of_landmarks(), weight) for i in range(p_n)]

    Q = np.matrix(np.diag([0.1, np.radians(1)]))
    R = np.matrix(np.diag([0.1, np.radians(1)]))

    hxEst = np.matrix(np.zeros((3, 1)))
    hxTrue = np.matrix(np.zeros((3, 1)))

    dt = 0.1
    t = 0
    error = []
    while t < 50:
        t += dt
        car.move(dt=dt)
        u = car.get_input()
        Z = car.observe(room)
        predict(particles, u, Q, dt)
        X_est, particles = update(particles, Z, R)
        X_true = car.get_state()
        hxEst = np.hstack((hxEst, X_est))
        hxTrue = np.hstack((hxTrue, car.get_state()))
        plt.cla()

        error.append((X_est[0, 0] - X_true[0, 0], X_est[1, 0] - X_true[1, 0]))
        # for i in range(len(Z)):
        #     plt.plot([X_true[0, 0], Z[i][1]], [X_true[1, 0], z_observed[i][2]], "-k")

        for landmark in room.get_landmarks():
            plt.plot(landmark[0], landmark[1], "*k")

        par_x = []
        par_y = []
        for particle in particles:
            state = particle.state()
            par_x.append(state[0,0])
            par_y.append(state[1, 0])
        plt.plot(par_x, par_y, ".r")

        plt.plot(np.array(hxTrue[0, :]).flatten(),
                 np.array(hxTrue[1, :]).flatten(), "-b")

        plt.plot(np.array(hxEst[0, :]).flatten(),
                 np.array(hxEst[1, :]).flatten(), "-r")
        plt.pause(0.001)

    n = len(error)
    X_error = [e[0] for e in error]
    Y_error = [e[1] for e in error]
    d_error = [ math.sqrt(e[0] * e[0] + e[1] * e[1]) for e in error]
    ind = range(n)

    plt.figure()
    plt.plot(ind, X_error, "b-", label="error_x")
    plt.plot(ind, Y_error, "g-", label="error_y")
    plt.plot(ind, d_error, "y-", label="error_dist")
    plt.legend()
    plt.show()


if __name__ == '__main__':
    main()