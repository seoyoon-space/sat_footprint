package footprint;

import org.orekit.bodies.GeodeticPoint;
import org.orekit.time.AbsoluteDate;

/**
 * Push-broom 한 라인의 footprint 결과 (좌우 끝점).
 */
public class FootprintResult {
    public final AbsoluteDate lineDate;
    public final GeodeticPoint leftPoint;
    public final GeodeticPoint rightPoint;

    public FootprintResult(AbsoluteDate lineDate, GeodeticPoint leftPoint, GeodeticPoint rightPoint) {
        this.lineDate = lineDate;
        this.leftPoint = leftPoint;
        this.rightPoint = rightPoint;
    }

    @Override
    public String toString() {
        return String.format(
            "FootprintResult[time=%s, left=(%.5f, %.5f, %.1fm), right=(%.5f, %.5f, %.1fm)]",
            lineDate,
            Math.toDegrees(leftPoint.getLatitude()), Math.toDegrees(leftPoint.getLongitude()), leftPoint.getAltitude(),
            Math.toDegrees(rightPoint.getLatitude()), Math.toDegrees(rightPoint.getLongitude()), rightPoint.getAltitude()
        );
    }
}
