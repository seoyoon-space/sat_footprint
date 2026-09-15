package footprint;

import org.hipparchus.geometry.euclidean.threed.Vector3D;
import org.orekit.data.DataContext;
import org.orekit.data.DirectoryCrawler;
import org.orekit.time.AbsoluteDate;
import org.orekit.time.TimeScalesFactory;

import java.io.File;
import java.io.IOException;
import java.io.PrintWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.List;

/**
 * CLI 진입점.
 *
 * args: <attitude_csv> <tile_index_json> <start_utc> <end_utc> <line_step> <output_csv>
 *       [orekit_data_path] [satellite_id] [sensor_calibration_json] [progress_json_path]
 *
 * attitude_csv: isoDate,px,py,pz,vx,vy,vz,q0,q1,q2,q3 (단위: m, m/s)
 * start_utc/end_utc: ISO8601 UTC (e.g. 2026-08-16T03:00:00)
 * line_step: 라인 계산 간격 (e.g. 100)
 * output_csv: 결과 저장 경로
 * orekit_data_path: (선택) Orekit 데이터 디렉토리, 기본값 "orekit-data-master"
 * satellite_id: (선택) 기본값 "O1A" — sensor_calibration_json에서 EOC 마운팅 보정
 *               unit vector를 찾을 때 쓰는 키.
 * sensor_calibration_json: (선택) data/sensor_calibration.json 경로. 생략하거나
 *               파일/위성 항목이 없으면 무보정(SensorCalibration 참고).
 * progress_json_path: (선택) 계산 도중 {"done":N,"total":M} 형태로 진행 상황을 주기적으로
 *               써주는 파일 경로. 생략하면 진행 상황을 기록하지 않는다 — attitude-viewer의
 *               /api/footprint/progress가 이 파일을 폴링해서 실제 진행률을 보여준다.
 */
public class Main {

    public static void main(String[] args) throws Exception {
        if (args.length < 6) {
            System.err.println("Usage: <attitude_csv> <tile_index> <start_utc> <end_utc> <line_step> <output_csv> [orekit_data]");
            System.exit(1);
        }

        String attCsvPath = args[0];
        String tileIndexPath = args[1];
        String startUtc = args[2];
        String endUtc = args[3];
        int lineStep = Integer.parseInt(args[4]);
        String outputCsv = args[5];
        String orekitDataPath = args.length > 6 ? args[6] : "orekit-data-master";
        String satelliteId = args.length > 7 && !args[7].isEmpty() ? args[7] : "O1A";
        String sensorCalibrationPath = args.length > 8 ? args[8] : null;
        String progressJsonPath = args.length > 9 && !args[9].isEmpty() ? args[9] : null;

        // Orekit 초기화
        File orekitData = new File(orekitDataPath);
        if (!orekitData.exists()) {
            System.err.println("Orekit data not found: " + orekitData.getAbsolutePath());
            System.exit(1);
        }
        DataContext.getDefault().getDataProvidersManager()
                .addProvider(new DirectoryCrawler(orekitData));

        // HK 데이터 로드
        List<AttitudeRecord> hkSamples = AttitudeRecord.loadFromCsv(attCsvPath);
        System.out.println("Loaded " + hkSamples.size() + " attitude samples");

        if (hkSamples.isEmpty()) {
            System.err.println("No samples in " + attCsvPath);
            System.exit(1);
        }

        // 센서 스펙 + HK 고도에서 lineRate 자동 계산
        SensorSpec baseSensor = SensorSpec.multiScape200Default();
        double meanAlt = SensorSpec.meanAltitude(hkSamples);
        double gsd = baseSensor.computeGsd(meanAlt);
        double computedLineRate = baseSensor.computeLineRate(meanAlt);
        SensorSpec sensor = baseSensor.withLineRate(computedLineRate);
        System.out.printf("Altitude=%.1f km, GSD=%.2f m, lineRate=%.1f lines/s%n",
                meanAlt / 1000.0, gsd, computedLineRate);

        AbsoluteDate startDate = new AbsoluteDate(
                hkSamples.get(0).isoDate, TimeScalesFactory.getUTC());
        AbsoluteDate stopDate = new AbsoluteDate(
                hkSamples.get(hkSamples.size() - 1).isoDate, TimeScalesFactory.getUTC());

        Vector3D eocMisalignment = SensorCalibration.loadMisalignmentVector(sensorCalibrationPath, satelliteId);
        if (eocMisalignment.getNorm() > 1e-9) {
            System.out.printf("EOC misalignment: satellite=%s vector=(%.6f, %.6f, %.6f)%n",
                    satelliteId, eocMisalignment.getX(), eocMisalignment.getY(), eocMisalignment.getZ());
        } else {
            System.out.println("EOC misalignment: none (satellite=" + satelliteId + ", 무보정)");
        }

        FootprintCalculator calculator = new FootprintCalculator(
                tileIndexPath, sensor, hkSamples, startDate, stopDate, eocMisalignment);

        // 계산 범위
        AbsoluteDate targetStart = new AbsoluteDate(startUtc, TimeScalesFactory.getUTC());
        AbsoluteDate targetEnd = new AbsoluteDate(endUtc, TimeScalesFactory.getUTC());

        int startLine = (int) (targetStart.durationFrom(startDate) * sensor.lineRate);
        int endLine = (int) (targetEnd.durationFrom(startDate) * sensor.lineRate);
        startLine = Math.max(0, startLine);

        System.out.printf("Computing lines %d ~ %d (step %d)%n", startLine, endLine, lineStep);

        int totalIterations = Math.max(1, (endLine - startLine) / lineStep + 1);
        int iterationIndex = 0;
        long lastProgressWriteMs = 0;

        // CSV 출력
        int count = 0;
        try (PrintWriter writer = new PrintWriter(outputCsv)) {
            writer.println("line,time,leftLat,leftLon,leftAlt,rightLat,rightLon,rightAlt");

            for (int line = startLine; line <= endLine; line += lineStep) {
                try {
                    FootprintResult result = calculator.compute(line);
                    writer.printf("%d,%s,%.6f,%.6f,%.1f,%.6f,%.6f,%.1f%n",
                            line, result.lineDate,
                            Math.toDegrees(result.leftPoint.getLatitude()),
                            Math.toDegrees(result.leftPoint.getLongitude()),
                            result.leftPoint.getAltitude(),
                            Math.toDegrees(result.rightPoint.getLatitude()),
                            Math.toDegrees(result.rightPoint.getLongitude()),
                            result.rightPoint.getAltitude());
                    count++;
                } catch (Exception e) {
                    System.err.println("Line " + line + " error: " + e.getMessage());
                }

                iterationIndex++;
                if (progressJsonPath != null) {
                    long now = System.currentTimeMillis();
                    // 200ms 간격으로 스로틀 — 매 라인마다 파일 쓰기를 하면 I/O가 계산
                    // 자체보다 비싸질 수 있어서, 마지막 라인은 항상 쓰되 그 사이는 시간
                    // 기준으로만 갱신한다.
                    if (now - lastProgressWriteMs >= 200 || iterationIndex == totalIterations) {
                        writeProgress(progressJsonPath, iterationIndex, totalIterations);
                        lastProgressWriteMs = now;
                    }
                }
            }
        }

        System.out.println("Saved " + count + " footprint lines to " + outputCsv);
    }

    /**
     * {"done": N, "total": M}을 progressJsonPath에 씀. 폴링하는 쪽(app.py)이 다 쓰다 만
     * 파일을 읽지 않도록, 임시 파일에 쓴 뒤 원자적으로 rename한다.
     */
    private static void writeProgress(String progressJsonPath, int done, int total) {
        try {
            Path target = Path.of(progressJsonPath);
            Path tmp = Path.of(progressJsonPath + ".tmp");
            String json = String.format("{\"done\":%d,\"total\":%d}", done, total);
            Files.writeString(tmp, json, StandardCharsets.UTF_8);
            Files.move(tmp, target, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
        } catch (IOException e) {
            // 진행률 표시는 부가 기능이라 실패해도 본 계산은 계속 진행한다.
            System.err.println("progress write failed: " + e.getMessage());
        }
    }
}
