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
 * DEM 결합 유무에 따른 footprint 오차를 비교하기 위한 1회성 테스트 진입점.
 *
 * Main.java와 같은 입력(attitude CSV, tile_index, 시간 구간)을 받아 각 라인에 대해
 * (1) DEM(ASTGTM) 지형 교차 결과와 (2) WGS84 타원체 전용(지형 무시) 결과를
 * 둘 다 계산하고, 좌/우 끝점의 수평/수직 차이를 CSV로 남긴다.
 *
 * args: <attitude_csv> <tile_index_json> <start_utc> <end_utc> <line_step> <output_csv>
 *       [orekit_data_path] [satellite_id] [sensor_calibration_json]
 */
public class CompareMain {

    private static final double EARTH_RADIUS_M = 6378137.0;

    public static void main(String[] args) throws Exception {
        if (args.length < 6) {
            System.err.println("Usage: <attitude_csv> <tile_index> <start_utc> <end_utc> <line_step> <output_csv> [orekit_data] [satellite_id] [sensor_calibration_json]");
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

        File orekitData = new File(orekitDataPath);
        if (!orekitData.exists()) {
            System.err.println("Orekit data not found: " + orekitData.getAbsolutePath());
            System.exit(1);
        }
        DataContext.getDefault().getDataProvidersManager()
                .addProvider(new DirectoryCrawler(orekitData));

        List<AttitudeRecord> hkSamples = AttitudeRecord.loadFromCsv(attCsvPath);
        System.out.println("Loaded " + hkSamples.size() + " attitude samples");
        if (hkSamples.isEmpty()) {
            System.err.println("No samples in " + attCsvPath);
            System.exit(1);
        }

        SensorSpec baseSensor = SensorSpec.multiScape200Default();
        double meanAlt = SensorSpec.meanAltitude(hkSamples);
        double computedLineRate = baseSensor.computeLineRate(meanAlt);
        SensorSpec sensor = baseSensor.withLineRate(computedLineRate);
        System.out.printf("Altitude=%.1f km, lineRate=%.1f lines/s%n", meanAlt / 1000.0, computedLineRate);

        AbsoluteDate startDate = new AbsoluteDate(hkSamples.get(0).isoDate, TimeScalesFactory.getUTC());
        AbsoluteDate stopDate = new AbsoluteDate(hkSamples.get(hkSamples.size() - 1).isoDate, TimeScalesFactory.getUTC());

        Vector3D eocMisalignment = SensorCalibration.loadMisalignmentVector(sensorCalibrationPath, satelliteId);

        FootprintCalculator calculator = new FootprintCalculator(
                tileIndexPath, sensor, hkSamples, startDate, stopDate, eocMisalignment);

        AbsoluteDate targetStart = new AbsoluteDate(startUtc, TimeScalesFactory.getUTC());
        AbsoluteDate targetEnd = new AbsoluteDate(endUtc, TimeScalesFactory.getUTC());

        int startLine = Math.max(0, (int) (targetStart.durationFrom(startDate) * sensor.lineRate));
        int endLine = (int) (targetEnd.durationFrom(startDate) * sensor.lineRate);

        System.out.printf("Computing lines %d ~ %d (step %d)%n", startLine, endLine, lineStep);

        int count = 0;
        try (PrintWriter writer = new PrintWriter(outputCsv)) {
            writer.println("line,time,"
                    + "demLeftLat,demLeftLon,demLeftAlt,flatLeftLat,flatLeftLon,flatLeftAlt,leftHorizErrM,leftVertErrM,"
                    + "demRightLat,demRightLon,demRightAlt,flatRightLat,flatRightLon,flatRightAlt,rightHorizErrM,rightVertErrM");

            for (int line = startLine; line <= endLine; line += lineStep) {
                try {
                    FootprintResult dem = calculator.compute(line);
                    FootprintResult flat = calculator.computeFlat(line);

                    double leftHoriz = haversineM(
                            dem.leftPoint.getLatitude(), dem.leftPoint.getLongitude(),
                            flat.leftPoint.getLatitude(), flat.leftPoint.getLongitude());
                    double rightHoriz = haversineM(
                            dem.rightPoint.getLatitude(), dem.rightPoint.getLongitude(),
                            flat.rightPoint.getLatitude(), flat.rightPoint.getLongitude());
                    double leftVert = dem.leftPoint.getAltitude() - flat.leftPoint.getAltitude();
                    double rightVert = dem.rightPoint.getAltitude() - flat.rightPoint.getAltitude();

                    writer.printf(
                            "%d,%s,%.6f,%.6f,%.1f,%.6f,%.6f,%.1f,%.2f,%.2f,%.6f,%.6f,%.1f,%.6f,%.6f,%.1f,%.2f,%.2f%n",
                            line, dem.lineDate,
                            Math.toDegrees(dem.leftPoint.getLatitude()), Math.toDegrees(dem.leftPoint.getLongitude()), dem.leftPoint.getAltitude(),
                            Math.toDegrees(flat.leftPoint.getLatitude()), Math.toDegrees(flat.leftPoint.getLongitude()), flat.leftPoint.getAltitude(),
                            leftHoriz, leftVert,
                            Math.toDegrees(dem.rightPoint.getLatitude()), Math.toDegrees(dem.rightPoint.getLongitude()), dem.rightPoint.getAltitude(),
                            Math.toDegrees(flat.rightPoint.getLatitude()), Math.toDegrees(flat.rightPoint.getLongitude()), flat.rightPoint.getAltitude(),
                            rightHoriz, rightVert);
                    count++;
                } catch (Exception e) {
                    System.err.println("Line " + line + " error: " + e.getMessage());
                }
            }
        }

        System.out.println("Saved " + count + " comparison lines to " + outputCsv);
    }

    /** 두 위경도(라디안) 사이의 대권거리(m). */
    private static double haversineM(double lat1, double lon1, double lat2, double lon2) {
        double dLat = lat2 - lat1;
        double dLon = lon2 - lon1;
        double a = Math.sin(dLat / 2) * Math.sin(dLat / 2)
                + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
        double c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
        return EARTH_RADIUS_M * c;
    }
}
