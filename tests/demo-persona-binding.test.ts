import { randomBytes } from "node:crypto";
import { describe, expect, it } from "vitest";

import {
  signDemoPersonaBinding,
  verifyDemoPersonaBinding
} from "../lib/demo-persona-binding.server";

describe("public demo persona binding", () => {
  it("binds the exact OAuth state and persona", () => {
    const environment = {
      APPSELL_SESSION_HMAC_KEY_B64: randomBytes(32).toString("base64")
    };
    const signature = signDemoPersonaBinding("state-1", "buyer", environment);

    expect(verifyDemoPersonaBinding("state-1", "buyer", signature, environment)).toBe(true);
    expect(verifyDemoPersonaBinding("state-1", "admin", signature, environment)).toBe(false);
    expect(verifyDemoPersonaBinding("state-2", "buyer", signature, environment)).toBe(false);
    expect(verifyDemoPersonaBinding("state-1", "buyer", null, environment)).toBe(false);
  });
});
