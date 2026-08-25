import numpy as np
from scipy.interpolate import interp1d

class BreakPoints:
    def __init__(self, nominal_traj_log, num_break_points: int, cum_velocity_interval: float):
        self.nominal_traj_log = nominal_traj_log
        self.num_break_points = num_break_points
        self.cum_velocity_interval = cum_velocity_interval

        self.cum_velocity = np.array([])
        self.time = np.array([])
        self.state = []
        self.control_input = []
        self.actuator_state = []
        self.state_matrix = []
        self.control_matrix = []
        self.gain = []

        self.generate_break_points()

    def generate_break_points(self):
        time_nominal = self.nominal_traj_log.time
        states = self.nominal_traj_log.states
        control_inputs = self.nominal_traj_log.control_inputs
        actuator_states = self.nominal_traj_log.actuator_states

        # 아포제 기점 크롭
        time_nominal, states, control_inputs, actuator_states = self.crop_to_apogee(
            time_nominal, states, control_inputs, actuator_states
        )

        # 지정된 인터벌로 리샘플링
        time_nominal, states, control_inputs, actuator_states = self.reduce_to_interval(
            time_nominal, states, control_inputs, actuator_states
        )

        # 누적 속도 계산 (vx, vy, vz 가정)
        vx = states[3, :]
        vy = states[4, :]
        vz = states[5, :]
        vel = np.sqrt(vx**2 + vy**2 + vz**2)
        cum_velocity_full = np.cumsum(vel * self.cum_velocity_interval)

        indices = np.round(np.linspace(0, len(cum_velocity_full) - 1, self.num_break_points)).astype(int)

        t_ignore = 1.0  # 초기 제어 권한 없는 구간 제외
        valid_mask = time_nominal[indices] > t_ignore
        indices = indices[valid_mask]
        num_valid_break_points = len(indices)

        print(f"BreakPoints: Filtered out {self.num_break_points - num_valid_break_points} breakpoints.")
        print(f"BreakPoints: Using {num_valid_break_points} breakpoints.")

        self.num_break_points = num_valid_break_points
        self.cum_velocity = np.zeros(self.num_break_points)
        self.time = np.zeros(self.num_break_points)
        self.state = [None] * self.num_break_points
        self.control_input = [None] * self.num_break_points
        self.actuator_state = [None] * self.num_break_points
        self.state_matrix = [[] for _ in range(self.num_break_points)]
        self.control_matrix = [[] for _ in range(self.num_break_points)]
        self.gain = [[] for _ in range(self.num_break_points)]

        for ii, idx in enumerate(indices):
            self.cum_velocity[ii] = cum_velocity_full[idx]
            self.time[ii] = time_nominal[idx]
            self.state[ii] = states[:, idx]
            self.control_input[ii] = control_inputs[:, idx]
            self.actuator_state[ii] = actuator_states[:, idx]

    def interpolate_gain(self, current_cum_velocity: float):
        if np.isnan(current_cum_velocity):
            raise ValueError("NaN currentCumVelocity")

        if current_cum_velocity <= self.cum_velocity[0]:
            return self.gain[0], self.control_input[0]
        elif current_cum_velocity >= self.cum_velocity[-1]:
            return self.gain[-1], self.control_input[-1]

        lower_idx = np.where(self.cum_velocity <= current_cum_velocity)[0][-1]
        upper_idx = lower_idx + 1

        cum_vel_lower = self.cum_velocity[lower_idx]
        cum_vel_upper = self.cum_velocity[upper_idx]

        lambda_val = (current_cum_velocity - cum_vel_lower) / (cum_vel_upper - cum_vel_lower)

        gains_lower = self.gain[lower_idx]
        gains_upper = self.gain[upper_idx]

        control_input_lower = self.control_input[lower_idx]
        control_input_upper = self.control_input[upper_idx]

        interpolated_gains = [(1 - lambda_val) * gl + lambda_val * gu for gl, gu in zip(gains_lower, gains_upper)]
        interpolated_control_reference = (1 - lambda_val) * control_input_lower + lambda_val * control_input_upper

        return interpolated_gains, interpolated_control_reference

    def reduce_to_interval(self, time, states, control_inputs, actuator_states):
        if self.cum_velocity_interval <= 0:
            return time, states, control_inputs, actuator_states

        time_new = np.arange(time[0], time[-1] + self.cum_velocity_interval, self.cum_velocity_interval)
        states_new = interp1d(time, states, axis=1, kind='linear', fill_value="extrapolate")(time_new)
        control_inputs_new = interp1d(time, control_inputs, axis=1, kind='linear', fill_value="extrapolate")(time_new)
        actuator_states_new = interp1d(time, actuator_states, axis=1, kind='linear', fill_value="extrapolate")(time_new)

        return time_new, states_new, control_inputs_new, actuator_states_new

    def crop_to_apogee(self, time, states, control_inputs, actuator_states):
        time_derivative = time[99:]
        z = states[2, 99:]
        dz = np.gradient(z, time_derivative)
        d2z = np.gradient(dz, time_derivative)

        index = np.where(np.abs(dz) < 0.1)[0]
        valid_idx = index[d2z[index] < 0]

        if len(valid_idx) > 0:
            apogee_idx = valid_idx[0] + 99
            time_of_apogee = time[apogee_idx]
            mask = time < time_of_apogee
            return time[mask], states[:, mask], control_inputs[:, mask], actuator_states[:, mask]
        else:
            return time, states, control_inputs, actuator_states