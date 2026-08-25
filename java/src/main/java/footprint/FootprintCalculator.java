package footprint;

import org.hipparchus.geometry.euclidean.threed.Vector3D;
import org.hipparchus.util.FastMath;
import org.orekit.bodies.GeodeticPoint;
import org.orekit.rugged.api.AlgorithmId;
import org.orekit.rugged.api.BodyRotatingFrameId;
import org.orekit.rugged.api.EllipsoidId;
import org.orekit.rugged.api.InertialFrameId;
import org.orekit.rugged.api.Rugged;
import org.orekit.rugged.api.RuggedBuilder;
import org.orekit.rugged.linesensor.LinearLineDatation;
import org.orekit.rugged.linesensor.LineSensor;
import org.orekit.rugged.los.FixedRotation;
import org.orekit.rugged.los.LOSBuilder;
import org.orekit.rugged.los.TimeDependentLOS;
import org.orekit.time.AbsoluteDate;
import org.orekit.utils.AngularDerivativesFilter;
import org.orekit.utils.CartesianDerivativesFilter;
import org.orekit.utils.TimeStampedAngularCoordinates;
import org.orekit.utils.TimeStampedPVCoordinates;

import java.util.ArrayList;
import java.util.List;

/**
 * Orekit/Rugged 기반 Footprint 계산.
 *
 * 입력: SensorSpec + AttitudeRecord 리스트 + DEM 타일
 * 출력: FootprintResult (라인별 좌우 지상 교점)
 *
 * 내부적으로 Orekit이 EME2000 → ITRF 정밀 변환을 처리하고,
 * Rugged가 DEM 지형과의 ray-terrain intersection을 수행합니다.
 */
public class FootprintCalculator {

    private final Rugged rugged;
    private final LineSensor lineSensor;

    public FootprintCalculator(String tileIndexJsonPath, SensorSpec sensor,
                                List<AttitudeRecord> hkSamples,
                                AbsoluteDate startDate, AbsoluteDate stopDate) throws Exception {

        // LOS (Line-of-Sight) 벡터 목록 구성
        List<Vector3D> rawDirs = new ArrayList<>();
        for (int i = 0; i < sensor.numPixels; i++) {
            double angle = (i - sensor.numPixels / 2.0) * FastMath.toRadians(sensor.fovDeg) / sensor.numPixels;
            rawDirs.add(new Vector3D(0d, FastMath.sin(angle), FastMath.cos(angle)));
        }
        LOSBuilder losBuilder = new LOSBuilder(rawDirs);
        losBuilder.addTransform(new FixedRotation("mounting", Vector3D.PLUS_I,
                FastMath.toRadians(sensor.mountingErrorDeg)));
        TimeDependentLOS los = losBuilder.build();

        // Datation + LineSensor
        LinearLineDatation datation = new LinearLineDatation(startDate, 0, sensor.lineRate);
        this.lineSensor = new LineSensor("mainSensor", datation, Vector3D.ZERO, los);

        // DEM 공급
        ASTGTMTileUpdater tileUpdater = new ASTGTMTileUpdater(tileIndexJsonPath);

        // HK → Orekit 타입 변환
        List<TimeStampedPVCoordinates> satellitePVList = HkToOrekitConverter.toPVList(hkSamples);
        List<TimeStampedAngularCoordinates> satelliteQList = HkToOrekitConverter.toAngularList(hkSamples);

        // Rugged 조립
        this.rugged = new RuggedBuilder()
                .setAlgorithm(AlgorithmId.DUVENHAGE)
                .setDigitalElevationModel(tileUpdater, 8)
                .setEllipsoid(EllipsoidId.WGS84, BodyRotatingFrameId.ITRF)
                .setTimeSpan(startDate, stopDate, 0.1, 1.0)
                .setTrajectory(InertialFrameId.EME2000,
                               satellitePVList, 6, CartesianDerivativesFilter.USE_PV,
                               satelliteQList, 8, AngularDerivativesFilter.USE_R)
                .addLineSensor(lineSensor)
                .build();
    }

    /**
     * 지정한 라인의 footprint (양쪽 끝 지상 교점)를 계산.
     */
    public FootprintResult compute(int line) {
        int firstPixel = 0;
        int lastPixel = lineSensor.getNbPixels() - 1;

        AbsoluteDate lineDate = lineSensor.getDate(line);
        Vector3D position = lineSensor.getPosition();
        Vector3D losLeft = lineSensor.getLOS(lineDate, firstPixel);
        Vector3D losRight = lineSensor.getLOS(lineDate, lastPixel);

        GeodeticPoint leftPoint = rugged.directLocation(lineDate, position, losLeft);
        GeodeticPoint rightPoint = rugged.directLocation(lineDate, position, losRight);

        return new FootprintResult(lineDate, leftPoint, rightPoint);
    }
}
