local function fmt(value)
  if value == nil then
    return "nan"
  end
  local numeric = tonumber(value)
  if numeric ~= nil then
    return tostring(numeric)
  end
  return tostring(value)
end

local function getenv(name, default)
  local value = os.getenv(name)
  if value == nil or value == "" then
    return default
  end
  return value
end

function done(summary, latency, requests)
  local target_qps = getenv("TARGET_QPS", "0")
  local actual_qps = nil
  if requests ~= nil then
    actual_qps = requests.rate or requests.throughput or requests.avg
  end

  local p50 = latency:percentile(50.0)
  local p95 = latency:percentile(95.0)
  local p99 = latency:percentile(99.0)
  local p999 = latency:percentile(99.9)

  io.write(string.format("Target QPS: %s\n", target_qps))
  io.write(string.format("Actual QPS: %s\n", fmt(actual_qps)))
  io.write(string.format("p50: %sus\n", fmt(p50)))
  io.write(string.format("p95: %sus\n", fmt(p95)))
  io.write(string.format("p99: %sus\n", fmt(p99)))
  io.write(string.format("p99.9: %sus\n", fmt(p999)))
  io.write(string.format("RESULT target_qps=%s actual_qps=%s p50_us=%s p95_us=%s p99_us=%s p999_us=%s\n", target_qps, fmt(actual_qps), fmt(p50), fmt(p95), fmt(p99), fmt(p999)))
end
