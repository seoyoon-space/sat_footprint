package footprint;

import java.io.BufferedReader;
import java.io.FileReader;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;

/**
 * 한 시점의 위성 자세 기록.
 *
 * 좌표계:
 *   position/velocity: ECI(≈EME2000), 단위 m / m/s
 *   quaternion: body-wrt-ECI, 스칼라 우선(q0=w)
 */
public class AttitudeRecord {
    public final String isoDate;
    public final double px, py, pz;
    public final double vx, vy, vz;
    public final double q0, q1, q2, q3;

    public AttitudeRecord(String isoDate,
                          double px, double py, double pz,
                          double vx, double vy, double vz,
                          double q0, double q1, double q2, double q3) {
        this.isoDate = isoDate;
        this.px = px; this.py = py; this.pz = pz;
        this.vx = vx; this.vy = vy; this.vz = vz;
        this.q0 = q0; this.q1 = q1; this.q2 = q2; this.q3 = q3;
    }

    /**
     * CSV 형식: isoDate,px,py,pz,vx,vy,vz,q0,q1,q2,q3
     */
    public static List<AttitudeRecord> loadFromCsv(String csvPath) throws IOException {
        List<AttitudeRecord> records = new ArrayList<>();
        try (BufferedReader br = new BufferedReader(new FileReader(csvPath))) {
            String header = br.readLine();
            if (header == null) throw new IOException("Empty CSV: " + csvPath);

            String line;
            while ((line = br.readLine()) != null) {
                String[] cols = line.split(",");
                if (cols.length < 11) continue;
                String isoDate = cols[0].replace("Z", "");
                records.add(new AttitudeRecord(
                    isoDate,
                    Double.parseDouble(cols[1]), Double.parseDouble(cols[2]), Double.parseDouble(cols[3]),
                    Double.parseDouble(cols[4]), Double.parseDouble(cols[5]), Double.parseDouble(cols[6]),
                    Double.parseDouble(cols[7]), Double.parseDouble(cols[8]),
                    Double.parseDouble(cols[9]), Double.parseDouble(cols[10])
                ));
            }
        }
        return records;
    }
}
