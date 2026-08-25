package footprint;

import org.orekit.rugged.raster.TileUpdater;
import org.orekit.rugged.raster.UpdatableTile;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.DataInputStream;
import java.io.FileInputStream;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.List;

/**
 * ASTGTMV003 DEM을 Rugged에 공급하는 TileUpdater.
 *
 * 데이터: tile_index.json + raw binary(.bin)
 *   .bin: float32, row-major, row0=South, col0=West (Rugged 규약 일치)
 */
public class ASTGTMTileUpdater implements TileUpdater {

    private final List<TileRecord> tiles;

    public ASTGTMTileUpdater(String tileIndexJsonPath) throws IOException {
        ObjectMapper mapper = new ObjectMapper();
        byte[] json = Files.readAllBytes(Paths.get(tileIndexJsonPath));
        this.tiles = List.of(mapper.readValue(json, TileRecord[].class));
    }

    @Override
    public void updateTile(double latitude, double longitude, UpdatableTile tile) {
        double latDeg = Math.toDegrees(latitude);
        double lonDeg = Math.toDegrees(longitude);

        TileRecord record = findCovering(latDeg, lonDeg);
        if (record == null) {
            int fallbackRows = 2;
            int fallbackCols = 2;
            double stepRad = Math.toRadians(1.0);
            double baseLat = Math.toRadians(Math.floor(latDeg));
            double baseLon = Math.toRadians(Math.floor(lonDeg));
            tile.setGeometry(baseLat, baseLon, stepRad, stepRad, fallbackRows, fallbackCols);
            for (int r = 0; r < fallbackRows; r++)
                for (int c = 0; c < fallbackCols; c++)
                    tile.setElevation(r, c, 0.0);
            return;
        }

        double minLatRad = Math.toRadians(record.minLat);
        double minLonRad = Math.toRadians(record.minLon);
        double latStepRad = Math.toRadians(record.latStepDeg);
        double lonStepRad = Math.toRadians(record.lonStepDeg);

        tile.setGeometry(minLatRad, minLonRad, latStepRad, lonStepRad,
                          record.rowsPx, record.colsPx);

        try (DataInputStream in = new DataInputStream(new FileInputStream(record.dataPath))) {
            byte[] buf = new byte[record.colsPx * 4];
            for (int latIndex = 0; latIndex < record.rowsPx; latIndex++) {
                in.readFully(buf);
                for (int lonIndex = 0; lonIndex < record.colsPx; lonIndex++) {
                    float elevation = bytesToFloatLE(buf, lonIndex * 4);
                    tile.setElevation(latIndex, lonIndex, elevation);
                }
            }
        } catch (IOException e) {
            throw new IllegalStateException("타일 데이터 읽기 실패: " + record.dataPath, e);
        }
    }

    private TileRecord findCovering(double latDeg, double lonDeg) {
        for (TileRecord t : tiles) {
            if (t.covers(latDeg, lonDeg)) return t;
        }
        return null;
    }

    private static float bytesToFloatLE(byte[] b, int offset) {
        int bits = (b[offset] & 0xFF)
                 | ((b[offset + 1] & 0xFF) << 8)
                 | ((b[offset + 2] & 0xFF) << 16)
                 | ((b[offset + 3] & 0xFF) << 24);
        return Float.intBitsToFloat(bits);
    }
}
