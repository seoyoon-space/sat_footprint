package footprint;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.hipparchus.geometry.euclidean.threed.Vector3D;

import java.io.File;
import java.io.IOException;

/**
 * data/sensor_calibration.json에서 위성별 EOC(카메라) 마운팅 보정 unit vector를 읽는다.
 *
 * 파일이 없거나 해당 위성 항목/필드가 없으면 Vector3D.ZERO를 반환 — FootprintCalculator는
 * norm이 0이면 무보정으로 해석해 회전 적용을 건너뛴다 (O1A는 아직 보정값이 없어 항상 이 경로).
 */
public final class SensorCalibration {

    // Jackson ObjectMapper는 생성 비용이 크고 스레드-세이프하므로 요청마다 새로 만들지
    // 않고 재사용한다.
    private static final ObjectMapper MAPPER = new ObjectMapper();

    private SensorCalibration() {
    }

    public static Vector3D loadMisalignmentVector(String jsonPath, String satelliteId) {
        if (jsonPath == null || jsonPath.isEmpty()) {
            return Vector3D.ZERO;
        }
        File file = new File(jsonPath);
        if (!file.exists()) {
            System.err.println("sensor_calibration.json 없음 (" + jsonPath + ") — 무보정으로 진행");
            return Vector3D.ZERO;
        }
        try {
            JsonNode root = MAPPER.readTree(file);
            JsonNode sat = root.get(satelliteId);
            if (sat == null) {
                // 위성 항목 자체가 없는 건 "아직 보정 안 됨"(예: O1A는 0,0,0으로 명시돼
                // 있음)과 달리 satelliteId 오타/미등록일 가능성이 있어 경고를 남긴다.
                System.err.println("sensor_calibration.json에 위성 '" + satelliteId + "' 항목이 없음 — 무보정으로 진행");
                return Vector3D.ZERO;
            }
            JsonNode vec = sat.get("eoc_misalignment_unit_vector");
            if (vec == null || !vec.isArray() || vec.size() < 3) {
                System.err.println("sensor_calibration.json의 '" + satelliteId
                        + "' 항목에 eoc_misalignment_unit_vector가 없거나 형식이 잘못됨 — 무보정으로 진행");
                return Vector3D.ZERO;
            }
            return new Vector3D(vec.get(0).asDouble(), vec.get(1).asDouble(), vec.get(2).asDouble());
        } catch (IOException e) {
            System.err.println("sensor_calibration.json 로드 실패 (" + jsonPath + "): " + e.getMessage());
            return Vector3D.ZERO;
        }
    }
}
