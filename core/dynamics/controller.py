# 오차 사원수 기반 피드백 제어기 및 휠 분배 행렬 (Allocation)

from abc import ABC, abstractmethod
import numpy as np

class Controller(ABC):
    """
    Abstract Base Class for Satellite Attitude Controllers.
    Adapted from MATLAB Controller class.
    """
    
    @abstractmethod
    def get_control(self, state, current_cum_velocity: float, current_time: float) -> np.ndarray:
        """
        Calculate control torque/input based on current state and accumulated wheel velocity.
        Must be implemented in a subclass.
        """
        pass

    @abstractmethod
    def populate_break_points(self) -> None:
        """
        Populate breakpoints for scheduled controllers.
        Must be implemented in a subclass.
        """
        pass

    @staticmethod
    def check_controllability(A: np.ndarray, B: np.ndarray) -> None:
        """
        Check linear system controllability using the controllability matrix.

        scipy.linalg에는 ctrb가 없어(=python-control 패키지 전용) 직접 구현:
        C = [B, A@B, A^2@B, ..., A^(n-1)@B]
        """
        n = A.shape[0]
        C = np.hstack([np.linalg.matrix_power(A, i) @ B for i in range(n)])
        r = np.linalg.matrix_rank(C)
        
        print(f"rank(C) = {r}")
        print(f"n       = {n}")
        
        if r == n:
            print("System is CONTROLLABLE.")
        else:
            print("System is NOT controllable.")