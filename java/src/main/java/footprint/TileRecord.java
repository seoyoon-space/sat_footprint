package footprint;

import com.fasterxml.jackson.annotation.JsonProperty;

public class TileRecord {
    public String path;
    @JsonProperty("data_path")    public String dataPath;
    public int row;
    public int col;
    @JsonProperty("min_lat")      public double minLat;
    @JsonProperty("max_lat")      public double maxLat;
    @JsonProperty("min_lon")      public double minLon;
    @JsonProperty("max_lon")      public double maxLon;
    @JsonProperty("lat_step_deg") public double latStepDeg;
    @JsonProperty("lon_step_deg") public double lonStepDeg;
    @JsonProperty("rows_px")      public int rowsPx;
    @JsonProperty("cols_px")      public int colsPx;
    @JsonProperty("overlap_cells") public int overlapCells;

    public boolean covers(double latDeg, double lonDeg) {
        return latDeg >= minLat && latDeg <= maxLat
            && lonDeg >= minLon && lonDeg <= maxLon;
    }
}
