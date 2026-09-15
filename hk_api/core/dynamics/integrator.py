# 0.05초~0.1초 타임스텝이 적용된 RK4/Euler 수치 적분기

import numpy as np

class Flight:
    def __init__(self, rocket_dynamics, environment, controller, navigation, flight_params):
        self.rocket_dynamics = rocket_dynamics
        self.environment = environment
        self.controller = controller
        self.navigation = navigation
        self.flight_params = flight_params

        self.dt = self.flight_params['dt']
        self.rocket_initial_state = np.array(self.flight_params['x0'])
        self.actuator_initial_state = np.array(self.flight_params['del0'])
        self.control_input_initial_state = np.array(self.flight_params['u0'])
        self.total_time = self.flight_params['totalTime']

        self.end_state = self.flight_params['endState']
        self.launch_rail_length = self.flight_params['launchRailLength']
        self.use_actuator_model = controller.use_actuator_model

        self.current_time = 0.0
        self.cum_velocity = 0.0
        self.flight_state = 1  # PRELAUNCH equivalent
        self.ground_altitude = 0.0

    def run_sim(self, logger):
        self.current_time = 0.0
        self.cum_velocity = 0.0
        rocket_state = self.rocket_initial_state.copy()
        actuator_state = self.actuator_initial_state.copy()
        control_input = self.control_input_initial_state.copy()
        
        logger.initialize_timestep(self.current_time)
        rocket_state_estimate = self.navigation.get_state_estimate(rocket_state, np.zeros(14), self.current_time, logger)

        logger.add(self.current_time, 'states', rocket_state)
        logger.add(self.current_time, 'actuatorStates', actuator_state)
        logger.add(self.current_time, 'controlInputs', control_input)
        logger.add(self.current_time, 'flightState', self.flight_state)

        ii = 0
        max_steps = logger.max_steps
        while self.flight_state != self.end_state and ii < max_steps - 1:
            ii += 1
            self.current_time = ii * self.dt
            logger.initialize_timestep(self.current_time)

            alt = rocket_state[2]  # Z position index assumption
            T, a, P, rho = self.environment.get_atmosphere(alt)
            wind = self.environment.get_wind_vector(alt)

            # 상태 머신별 시뮬레이션 루프 분기 처리
            if self.flight_state == 1:  # PRELAUNCH
                rocket_state = self.rocket_initial_state.copy()
                logger.add(self.current_time, 'states', rocket_state)
                self.update_flight_state(self.current_time, rocket_state, np.zeros_like(rocket_state))

            elif self.flight_state == 2:  # LAUNCH_RAIL
                # 레일 위 오픈루프 및 적분 처리
                pass  # 추가 동역학 연동부 확장 지점

            elif self.flight_state in [3, 4]:  # POWERED/COASTING ASCENT
                if self.use_actuator_model:
                    control_input = self.controller.get_control(rocket_state_estimate, self.cum_velocity, self.current_time)
                
                logger.add(self.current_time, 'states', rocket_state)
                logger.add(self.current_time, 'controlInputs', control_input)
                self.update_flight_state(self.current_time, rocket_state, np.zeros_like(rocket_state))

        return logger

    def update_flight_state(self, time, rocket_state, rocket_state_dot):
        if self.flight_state == 1 and time >= self.rocket_dynamics.t_ignition:
            self.flight_state = 2  # LAUNCH_RAIL로 전이
        # 추가 상태 전이 조건문 매핑 구현 영역