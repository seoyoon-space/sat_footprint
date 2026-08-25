package footprint;

/**
 * 센서 광학 스펙.
 *
 * lineRate는 고정값이 아니라 HK 고도에서 동적 계산:
 *   IFOV = pixelSize / focalLength
 *   GSD  = IFOV × altitude
 *   lineRate = FMC_GROUND_SPEED / GSD
 */
public class SensorSpec {

    public static final double FMC_GROUND_SPEED_MPS = 3906.173;
    public static final double EARTH_RADIUS_M = 6378137.0;

    public final double fovDeg;
    public final int numPixels;
    public final double mountingErrorDeg;
    public final double lineRate;
    public final double focalLengthMm;
    public final double pixelSizeUm;

    public SensorSpec(double fovDeg, int numPixels, double mountingErrorDeg,
                      double lineRate, double focalLengthMm, double pixelSizeUm) {
        this.fovDeg = fovDeg;
        this.numPixels = numPixels;
        this.mountingErrorDeg = mountingErrorDeg;
        this.lineRate = lineRate;
        this.focalLengthMm = focalLengthMm;
        this.pixelSizeUm = pixelSizeUm;
    }

    public double ifovRad() {
        return (pixelSizeUm / 1000.0) / focalLengthMm;
    }

    public double computeGsd(double altitudeM) {
        return ifovRad() * altitudeM;
    }

    public double computeLineRate(double altitudeM) {
        return FMC_GROUND_SPEED_MPS / computeGsd(altitudeM);
    }

    public SensorSpec withLineRate(double newLineRate) {
        return new SensorSpec(fovDeg, numPixels, mountingErrorDeg, newLineRate,
                              focalLengthMm, pixelSizeUm);
    }

    public static SensorSpec fromOpticalSpec(double focalLengthMm, double pixelSizeUm,
                                              double fovAcrossDeg, double mountingErrorDeg) {
        double ifovRad = (pixelSizeUm / 1000.0) / focalLengthMm;
        int numPixels = (int) Math.round(Math.toRadians(fovAcrossDeg) / ifovRad);
        return new SensorSpec(fovAcrossDeg, numPixels, mountingErrorDeg, 0.0,
                              focalLengthMm, pixelSizeUm);
    }

    public static double meanAltitude(java.util.List<AttitudeRecord> samples) {
        double sum = 0;
        for (AttitudeRecord r : samples) {
            double dist = Math.sqrt(r.px * r.px + r.py * r.py + r.pz * r.pz);
            sum += dist - EARTH_RADIUS_M;
        }
        return sum / samples.size();
    }

    public static SensorSpec multiScape200Default() {
        return fromOpticalSpec(1067.0, 3.2, 1.6, 0.0);
    }
}
