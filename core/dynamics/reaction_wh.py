import numpy as np
from core.dynamics.actuactor import ActuatorModel  # 파일명이 actuactor.py(오타)이므로 import도 맞춤

class ReactionWheel(ActuatorModel):
    """
    Reaction Wheel Actuator Model for Satellite FSW and Simulation API.
    Handles torque limits, max RPM limits, Back-EMF torque attenuation, 
    and wheel momentum integration.

    주의: get_forces_moments()는 current_cg/env_conditions를 받는 로켓
    ActuatorModel 인터페이스를 그대로 상속했지만 반작용휠에는 의미 없는
    값입니다(호환성을 위해 시그니처만 유지, 내부에서 사용 안 함).
    현재 텔레메트리 기반 재구성 경로(core/reconstruction)는 이 클래스를
    쓰지 않고 필요한 값을 텔레메트리에서 직접 계산합니다.
    """
    def __init__(self, allocation_matrix: np.ndarray, max_torque: float = 0.05, max_rpm: float = 5000.0, inertia_wheel: float = 1e-4):
        self.allocation_matrix = allocation_matrix          # 휠 분배 행렬 (Body Torque <-> Wheel Torques)
        self.max_torque = max_torque                        # 최대 토크 한계치 [Nm] (예: 0.05 Nm)
        self.max_rpm = max_rpm                              # 최고 회전속도 한계치 [RPM] (예: 5000 RPM)
        self.max_rad_s = max_rpm * (2 * np.pi / 60)         # 최고 회전속도 [rad/s]
        self.inertia_wheel = inertia_wheel                  # 휠 회전관성모멘트 I_w [kg m^2]

    def get_forces_moments(self, state: np.ndarray, actuator_state: np.ndarray, current_cg: np.ndarray, env_conditions) -> tuple[np.ndarray, np.ndarray]:
        """
        위성 바디에 작용하는 반작용 토크(Moments) 계산.
        반응휠이 구동할 때 발생하는 토크는 위성 본체에 반작용으로 작용함.

        TODO: _compute_current_torques()가 현재 항상 0을 반환하는 스텁입니다.
        순방향(forward) 시뮬레이션에 이 클래스를 실제로 쓰려면, 휠 각가속도
        이력(actuator_dynamics 호출 결과)을 상태로 들고 있다가 토크로 환산하는
        로직을 채워야 합니다.
        """
        fB = np.zeros(3)  # 순수 자세 제어 휠

        omega_w = actuator_state

        wheel_torques = self._compute_current_torques(omega_w)
        mB = -self.allocation_matrix @ wheel_torques
        
        return fB, mB

    def actuator_dynamics(self, control_input: np.ndarray, actuator_state: np.ndarray) -> np.ndarray:
        """
        반응휠 동역학 미분 방정식 산출 (가속도 계산: Omega_dot)
        control_input: 제어기로부터 전달받은 각 휠의 구동 토크 명령 [Nm]
        actuator_state: 각 휠의 현재 회전속도 [rad/s] (Omega_w)
        """
        torques = np.clip(control_input, -self.max_torque, self.max_torque)

        speed_ratio = np.abs(actuator_state) / self.max_rad_s
        back_emf_attenuation = np.clip(1.0 - speed_ratio, 0.0, 1.0)
        actual_torques = torques * back_emf_attenuation

        saturated = (np.abs(actuator_state) >= self.max_rad_s) & (np.sign(control_input) == np.sign(actuator_state))
        actual_torques[saturated] = 0.0

        actuator_state_dot = actual_torques / self.inertia_wheel
        
        return actuator_state_dot

    def _compute_current_torques(self, omega_w: np.ndarray) -> np.ndarray:
        # TODO: 내부 휠 상태 기반 토크 연산 보조 함수 - 현재 미구현(항상 0)
        return np.zeros_like(omega_w)

    def calculate_wheel_momentum(self, actuator_state: np.ndarray) -> np.ndarray:
        """
        휠 각운동량 적분 수식 연산: h_w = I_w * Omega_w
        지상 관제 시 휠 텔레메트리 모니터링 및 포화(Saturation) 여부 스캔 시 활용
        """
        return self.inertia_wheel * actuator_state