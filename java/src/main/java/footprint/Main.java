package footprint;

import org.hipparchus.geometry.euclidean.threed.Vector3D;
import org.orekit.data.DataContext;
import org.orekit.data.DirectoryCrawler;
import org.orekit.time.AbsoluteDate;
import org.orekit.time.TimeScalesFactory;

import java.io.File;
import java.io.PrintWriter;
import java.util.List;

/**
 * CLI 진입점.
 *
 * args: <attitude_csv> <tile_index_json> <start_utc> <end_utc> <line_step> <output_csv>
 *       [orekit_data_path] [satellite_id] [sensor_calibration_json]
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
            }
        }

        System.out.println("Saved " + count + " footprint lines to " + outputCsv);
    }
}
