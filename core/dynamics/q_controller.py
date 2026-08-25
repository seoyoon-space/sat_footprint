import numpy as np
from scipy.linalg import solve_continuous_are
from core.dynamics.controller import Controller

class DecoupledLQR(Controller):
    def __init__(self, break_points, reference_attitudes, q2, q3, r2, r3, reduce_lti, use_actuator_model=False):
        self.break_points = break_points
        self.reference_attitudes = reference_attitudes
        self.Q2 = q2
        self.Q3 = q3
        self.R2 = r2
        self.R3 = r3
        self.reduce_lti = reduce_lti
        self.use_actuator_model = use_actuator_model

    def get_control(self, state: np.ndarray, current_cum_velocity: float, current_time: float) -> np.ndarray:
        interpolated_gains, interpolated_control_reference = self.break_points.interpolate_gain(current_cum_velocity)
        
        K2 = interpolated_gains[0]
        K3 = interpolated_gains[1]
        
        roll_control_ref = interpolated_control_reference[0]
        pitch_control_ref = interpolated_control_reference[1]
        yaw_control_ref = interpolated_control_reference[2]
        
        roll_err, pitch_err, yaw_err = self.get_error(state, current_time)
        
        roll_control = roll_control_ref + K2 @ roll_err
        pitch_control = pitch_control_ref + K3 @ pitch_err
        yaw_control = yaw_control_ref + K3 @ yaw_err
        
        rocket_control_rad = np.array([roll_control, pitch_control, yaw_control]).flatten()
        return np.rad2deg(rocket_control_rad)

    def get_error(self, rocket_state_estimate: np.ndarray, current_time: float):
        x3_pitch = self.reduce_lti.pitch_reduce(rocket_state_estimate)
        x3_yaw = self.reduce_lti.pitch_reduce(rocket_state_estimate, yaw=True)
        x2_roll = self.reduce_lti.roll_reduce(rocket_state_estimate)

        time_vec = self.reference_attitudes[0, :]
        idx = np.argmin(np.abs(time_vec - current_time))
        
        roll_ref = self.reference_attitudes[1, idx]
        pitch_ref = self.reference_attitudes[2, idx]
        yaw_ref = self.reference_attitudes[3, idx]
        
        roll_err = np.array([0.0, roll_ref]) - x2_roll
        pitch_err = np.array([0.0, 0.0, pitch_ref]) - x3_pitch
        yaw_err = np.array([0.0, 0.0, yaw_ref]) - x3_yaw
        
        return roll_err, pitch_err, yaw_err

    def populate_break_points(self):
        for ii in range(self.break_points.num_break_points):
            time = self.break_points.time[ii]
            state = self.break_points.state[ii]
            control_input = self.break_points.control_input[ii]
            actuator_state = self.break_points.actuator_state[ii]
            
            A3, B3 = self.reduce_lti.calculate_rom_pitch(state, control_input[1], actuator_state, time, self.use_actuator_model)
            A2, B2 = self.reduce_lti.calculate_rom_roll(state, control_input[0], actuator_state, time, self.use_actuator_model)
            
            A3[:, 2] = 0.0
            A3[0, 1] = 1.0

            # Riccati 방정식 풀이를 통한 LQR 게인 산출
            P3 = solve_continuous_are(A3, B3, self.Q3, self.R3)
            K3 = np.linalg.inv(self.R3) @ B3.T @ P3

            P2 = solve_continuous_are(A2, B2, self.Q2, self.R2)
            K2 = np.linalg.inv(self.R2) @ B2.T @ P2
            
            self.break_points.state_matrix[ii] = [A2, A3]
            self.break_points.control_matrix[ii] = [B2, B3]
            self.break_points.gain[ii] = [K2, K3]