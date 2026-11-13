-- severity: warn
-- Recordings with lines that are not valid JSON (usually a line cut off when the recorder stopped).
SELECT slug, bad_lines, lines FROM raw_markets WHERE bad_lines > 0;
