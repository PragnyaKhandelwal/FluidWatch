import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  DEFAULT_THRESHOLDS,
  INITIAL_BEDS,
  applyLiveUpdate,
  deriveHttpOrigin,
  deriveStatus,
  fetchDevices,
  fetchReadings,
  formatFlow,
  frameImageUrl,
  tick,
  type Bed,
} from "@/lib/fluidwatch";

function bed(overrides: Partial<Bed> = {}): Bed {
  return {
    id: "01",
    bed: "Bed 01",
    patient: "J. Marsh",
    fluid: "Saline 0.9%",
    flow: 100,
    level: 80,
    status: "STABLE",
    muted: false,
    updatedAt: 0,
    auxClass: null,
    history: [80],
    battery: null,
    wifi: null,
    ...overrides,
  };
}

/** Exactly what server/app/bedfeed.py's to_bed_payload() sends. */
const backendPayload = {
  id: "IV-STAND-01",
  bed: "Bed 07",
  patient: "Test Patient",
  fluid: "Saline 0.9%",
  flow: 120,
  level: 55.5,
  status: "STABLE",
  auxClass: "50%",
  battery: 88,
  wifi: -61,
};

describe("deriveStatus", () => {
  it.each([
    [80, 100, "STABLE"],
    [35, 100, "WATCH"],
    [36, 100, "STABLE"],
    [10, 100, "CRITICAL"],
    [80, 15, "CRITICAL"], // flow at/below minFlow is critical even with a full bag
    [80, 16, "STABLE"],
  ] as const)("level=%d flow=%d -> %s", (level, flow, expected) => {
    expect(deriveStatus(level, flow)).toBe(expected);
  });

  it("respects ward-configured thresholds", () => {
    const strict = { watchLevel: 60, criticalLevel: 40, minFlow: 0 };
    expect(deriveStatus(50, 100, strict)).toBe("WATCH");
    expect(deriveStatus(40, 100, strict)).toBe("CRITICAL");
    expect(deriveStatus(80, 1, strict)).toBe("STABLE");
  });
});

describe("applyLiveUpdate", () => {
  it("adds a never-seen device as a new bed card", () => {
    const beds = applyLiveUpdate(INITIAL_BEDS, backendPayload);
    expect(beds).toHaveLength(INITIAL_BEDS.length + 1);
    const added = beds.find((b) => b.id === "IV-STAND-01")!;
    expect(added).toMatchObject({
      bed: "Bed 07",
      level: 55.5,
      flow: 120,
      status: "STABLE",
      auxClass: "50%",
      battery: 88,
      wifi: -61,
      muted: false,
      history: [55.5],
    });
  });

  it("updates an existing bed in place and keeps local UI state", () => {
    const beds = applyLiveUpdate([bed({ muted: true, battery: 50 })], { id: "01", level: 70 });
    expect(beds).toHaveLength(1);
    expect(beds[0]).toMatchObject({
      level: 70,
      flow: 100,
      muted: true,
      battery: 50,
      history: [80, 70],
    });
  });

  it("derives status when the payload omits it, using thresholds", () => {
    const [b] = applyLiveUpdate([], { id: "X", level: 20, flow: 100 });
    expect(b!.status).toBe("WATCH");
    const [c] = applyLiveUpdate(
      [],
      { id: "X", level: 20, flow: 100 },
      { ...DEFAULT_THRESHOLDS, criticalLevel: 25 },
    );
    expect(c!.status).toBe("CRITICAL");
  });

  it("accepts an array of rows", () => {
    const beds = applyLiveUpdate(
      [],
      [
        { id: "A", level: 50 },
        { id: "B", level: 60 },
      ],
    );
    expect(beds.map((b) => b.id)).toEqual(["A", "B"]);
  });

  it.each([null, undefined, 42, "text", {}, { id: "" }, { id: "   " }])(
    "ignores malformed row %j",
    (row) => {
      expect(applyLiveUpdate([bed()], row)).toEqual([bed()]);
    },
  );

  it("fills defaults for a minimal payload", () => {
    const [b] = applyLiveUpdate([], { id: 7 });
    expect(b).toMatchObject({
      id: "7",
      bed: "Device 7",
      patient: "Unassigned",
      fluid: "Unknown",
      level: 0,
      flow: 0,
    });
  });

  it("rejects unknown aux classes and non-numeric telemetry", () => {
    const [b] = applyLiveUpdate([bed({ auxClass: "full", battery: 40 })], {
      id: "01",
      auxClass: "bogus",
      battery: "90",
      level: "70",
    });
    expect(b!.auxClass).toBe("full");
    expect(b!.battery).toBe(40);
    expect(b!.level).toBe(80);
  });

  it("caps sparkline history at 30 points", () => {
    let beds = [bed({ history: [] })];
    for (let i = 0; i < 40; i++) beds = applyLiveUpdate(beds, { id: "01", level: i });
    expect(beds[0]!.history).toHaveLength(30);
    expect(beds[0]!.history[0]).toBe(10);
    expect(beds[0]!.history.at(-1)).toBe(39);
  });

  it("does not mutate the input array", () => {
    const input = [bed()];
    const snapshot = structuredClone(input);
    applyLiveUpdate(input, { id: "01", level: 1 });
    expect(input).toEqual(snapshot);
  });
});

describe("tick (simulated stream)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("drains level, never below zero, and stops flow when empty", () => {
    vi.spyOn(Math, "random").mockReturnValue(0.99);
    let beds = [bed({ level: 2, flow: 50 })];
    for (let i = 0; i < 5; i++) beds = tick(beds);
    expect(beds[0]!.level).toBe(0);
    expect(beds[0]!.flow).toBe(0);
    expect(beds[0]!.status).toBe("CRITICAL");
  });

  it("leaves a stopped drip alone", () => {
    const [b] = tick([bed({ level: 40, flow: 0 })]);
    expect(b!.level).toBe(40);
    expect(b!.flow).toBe(0);
  });
});

describe("deriveHttpOrigin", () => {
  it.each([
    ["ws://localhost:8000/ws/bedfeed", "http://localhost:8000"],
    ["wss://iv.onrender.com/ws/bedfeed?x=1", "https://iv.onrender.com"],
    ["ws://192.168.1.20:8000", "http://192.168.1.20:8000"],
  ])("%s -> %s", (ws, http) => {
    expect(deriveHttpOrigin(ws)).toBe(http);
  });

  it("returns null for an unparseable URL", () => {
    expect(deriveHttpOrigin("not a url")).toBeNull();
  });
});

describe("frameImageUrl", () => {
  it("encodes the device id and cache-busts", () => {
    expect(frameImageUrl("http://h:8000", "CAM 01/x", 123)).toBe(
      "http://h:8000/api/v1/frames/latest?device_id=CAM%2001%2Fx&t=123",
    );
  });

  it("returns null without an origin", () => {
    expect(frameImageUrl(null, "CAM", 1)).toBeNull();
  });
});

describe("formatFlow", () => {
  it.each([
    [124, "ml", "124"],
    [124, "l", "0.12"],
    [120, "drops", "40"],
  ] as const)("%d ml/hr in %s -> %s", (flow, unit, expected) => {
    expect(formatFlow(flow, unit)).toBe(expected);
  });
});

describe("REST fetchers", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("skip the network without an origin", async () => {
    expect(await fetchDevices(null)).toEqual([]);
    expect(await fetchReadings(null)).toEqual([]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("fetchDevices hits /api/v1/devices", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => [{ device_id: "A" }] });
    expect(await fetchDevices("http://h")).toEqual([{ device_id: "A" }]);
    expect(fetchMock).toHaveBeenCalledWith("http://h/api/v1/devices");
  });

  it("fetchReadings builds the query string", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => [] });
    await fetchReadings("http://h", "CAM-1", 50);
    expect(fetchMock).toHaveBeenCalledWith("http://h/api/v1/readings?limit=50&device_id=CAM-1");
    await fetchReadings("http://h");
    expect(fetchMock).toHaveBeenLastCalledWith("http://h/api/v1/readings?limit=500");
  });

  it("throw on a non-2xx response", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 503 });
    await expect(fetchDevices("http://h")).rejects.toThrow("503");
    await expect(fetchReadings("http://h")).rejects.toThrow("503");
  });
});
