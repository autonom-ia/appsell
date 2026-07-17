import Redis from "ioredis";

const url = required("APPSELL_REDIS_URL");
const prefix = required("APPSELL_REDIS_PREFIX");
const providerCredential = /demo-(?:access|id|refresh)-[A-Za-z0-9_-]{20,}/i;
const redis = new Redis(url, { lazyConnect: true, maxRetriesPerRequest: 1 });
let inspected = 0;

try {
  await redis.connect();
  let cursor = "0";
  do {
    const [nextCursor, keys] = await redis.scan(cursor, "MATCH", `${prefix}*`, "COUNT", 100);
    cursor = nextCursor;
    for (const key of keys) {
      const type = await redis.type(key);
      if (type !== "string") throw new Error(`Unexpected Redis type ${type} for ${key}`);
      const value = await redis.get(key);
      if (providerCredential.test(key) || providerCredential.test(value ?? "")) {
        throw new Error(`Provider credential reached Redis key ${key}`);
      }
      inspected += 1;
    }
  } while (cursor !== "0");
  if (inspected < 2) throw new Error("Redis preview evidence was unexpectedly empty.");
  process.stdout.write("Redis preview scan is clean.\n");
} finally {
  redis.disconnect();
}

function required(name) {
  const value = process.env[name];
  if (!value?.trim()) throw new Error(`${name} is required.`);
  return value;
}
