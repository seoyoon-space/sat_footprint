package footprint;

import org.hipparchus.geometry.euclidean.threed.Rotation;
import org.hipparchus.geometry.euclidean.threed.Vector3D;
import org.orekit.time.AbsoluteDate;
import org.orekit.time.TimeScalesFactory;
import org.orekit.utils.TimeStampedAngularCoordinates;
import org.orekit.utils.TimeStampedPVCoordinates;

import java.util.ArrayList;
import java.util.List;

/**
 * AttitudeRecord → Orekit 타입 변환.
 *
 * HK의 posWrtEci/velWrtEci는 이미 ECI(≈EME2000) 좌표계이므로,
 * 좌표 변환 없이 직접 전달합니다.
 */
public class HkToOrekitConverter {

    public static List<TimeStampedPVCoordinates> toPVList(List<AttitudeRecord> samples) throws Exception {
        List<TimeStampedPVCoordinates> result = new ArrayList<>(samples.size());
        for (AttitudeRecord s : samples) {
            AbsoluteDate date = new AbsoluteDate(s.isoDate, TimeScalesFactory.getUTC());
            Vector3D position = new Vector3D(s.px, s.py, s.pz);
            Vector3D velocity = new Vector3D(s.vx, s.vy, s.vz);
            result.add(new TimeStampedPVCoordinates(date, position, velocity));
        }
        return result;
    }

    public static List<TimeStampedAngularCoordinates> toAngularList(List<AttitudeRecord> samples) {
        List<TimeStampedAngularCoordinates> result = new ArrayList<>(samples.size());
        for (AttitudeRecord s : samples) {
            AbsoluteDate date = new AbsoluteDate(s.isoDate, TimeScalesFactory.getUTC());
            Rotation rotation = new Rotation(s.q0, s.q1, s.q2, s.q3, true);
            result.add(new TimeStampedAngularCoordinates(date, rotation, Vector3D.ZERO, Vector3D.ZERO));
        }
        return result;
    }
}
