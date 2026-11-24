import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page } from "@playwright/test";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// Runs against e2e/fake_api.py (see playwright.config.ts). Every upload gets a unique file name, because
// the backend's upload list is shared by all tests.

const FAKE_API = "http://127.0.0.1:8765";
const ZIP = "application/zip";

let seq = 0;
const unique = (stem: string) => `${stem}-${Date.now()}-${++seq}.zip`;

/** Upload files whose bytes tell the fake API how to behave ("SCENARIO:<name>:<nonce>"). */
async function upload(page: Page, scenarios: { scenario: string; name?: string }[]): Promise<string[]> {
  const files = scenarios.map(({ scenario, name }) => ({
    name: name ?? unique(scenario),
    mimeType: ZIP,
    buffer: Buffer.from(`SCENARIO:${scenario}:${Date.now()}-${Math.random()}`),
  }));
  await page.getByLabel("Choose zip files to upload").setInputFiles(files);
  return files.map((f) => f.name);
}

const row = (page: Page, name: string): Locator => page.locator(".run", { hasText: name });

/** Count canvas pixels by overlay colour. The CT itself is grey (r = g = b), so any tint is a mask. */
async function tints(page: Page) {
  return page.getByLabel("Axial CT slice").evaluate((c: HTMLCanvasElement) => {
    const d = c.getContext("2d")!.getImageData(0, 0, c.width, c.height).data;
    let amber = 0;
    let sky = 0;
    let teal = 0;
    for (let i = 0; i < d.length; i += 4) {
      const [r, g, b] = [d[i], d[i + 1], d[i + 2]];
      if (r - b > 40) amber++; // model fill
      else if (b - r > 60 && b >= g) sky++; // reference outline
      else if (g - r > 40 && g > b) teal++; // both, in the agreement view
    }
    return { amber, sky, teal };
  });
}

async function openApp(page: Page) {
  await page.goto("/");
  await expect(page.getByLabel("Axial CT slice")).toBeVisible();
}

test("shows the CT in a soft-tissue window and moves through slices", async ({ page }) => {
  await openApp(page);
  await expect(page.getByText("Slice 91 of 180")).toBeVisible();
  await expect(page.getByText("W 400 L 40")).toBeVisible();
  for (const side of ["R", "L", "A", "P"]) await expect(page.locator(".edge", { hasText: new RegExp(`^${side}$`) })).toBeVisible();

  const lit = await page.getByLabel("Axial CT slice").evaluate((c: HTMLCanvasElement) => {
    const d = c.getContext("2d")!.getImageData(0, 0, c.width, c.height).data;
    let n = 0;
    for (let i = 0; i < d.length; i += 4) if (d[i] > 60) n++;
    return n;
  });
  expect(lit).toBeGreaterThan(20_000); // a real image, not a blank canvas

  await page.keyboard.press("ArrowUp");
  await expect(page.getByText("Slice 92 of 180")).toBeVisible();
  await page.getByLabel("Axial CT slice").hover();
  await page.mouse.wheel(0, 100);
  await expect(page.getByText("Slice 93 of 180")).toBeVisible();
  await page.getByLabel("Slice", { exact: true }).fill("10");
  await expect(page.getByText("Slice 11 of 180")).toBeVisible();

  await page.getByRole("radio", { name: "Bone" }).check();
  await expect(page.getByText("W 1800 L 400")).toBeVisible();
});

test("uploads case-001, overlays each returned mask with its own switch, and shows Dice", async ({ page }) => {
  await openApp(page);
  await page.getByRole("button", { name: "Upload case-001" }).click();
  await expect(page.locator(".run").first()).toContainText(/Uploading|Segmenting|Waiting for a free upload slot/);

  await expect(page.locator(".dsc")).toHaveText(/^0\.\d{3}$/, { timeout: 30_000 });
  await expect(page.getByText("Grid check passed: 416 × 350 × 180 voxels, same affine.")).toBeVisible();
  await expect(page.getByText("9 of 10")).toBeVisible(); // the fake model misses the smallest lesion
  const model = page.getByRole("switch", { name: "Model: lesions" });
  const reference = page.getByRole("switch", { name: "Reference (ground truth)" });
  await expect(model).toBeChecked();
  await expect(reference).toBeChecked();

  // Jump to a slice with lesions by clicking the lesion strip.
  const bars = page.locator('[data-lane="Model"] rect');
  await bars.nth(Math.floor((await bars.count()) / 2)).click({ force: true });
  await expect.poll(async () => (await tints(page)).amber).toBeGreaterThan(100);
  await expect.poll(async () => (await tints(page)).sky).toBeGreaterThan(20);

  await model.uncheck();
  await expect.poll(async () => (await tints(page)).amber).toBe(0);
  await reference.uncheck();
  await expect.poll(async () => (await tints(page)).sky).toBe(0);

  await model.check();
  await reference.check();
  await page.getByRole("radio", { name: "Agreement" }).check();
  await expect.poll(async () => (await tints(page)).teal).toBeGreaterThan(100);
});

test("explains failed uploads in plain language, with the technical detail tucked away", async ({ page }) => {
  await openApp(page);
  const png = unique("photo").replace(".zip", ".png");
  await page.getByLabel("Choose zip files to upload").setInputFiles({ name: png, mimeType: "image/png", buffer: Buffer.from("\x89PNG not a zip") });
  const failed = row(page, png);
  await expect(failed).toContainText("Failed");
  await expect(failed).toContainText("That file isn't a valid zip archive.");
  await expect(failed.locator("details")).not.toHaveAttribute("open", "");
  await failed.getByText("Technical details").click();
  await expect(failed.locator("code")).toContainText("not_zip");

  const cases: [string, string][] = [
    ["incomplete", "The series is missing slices."],
    ["unknown", "The service doesn't recognise this scan."],
    ["not-dicom", "The zip contains files that aren't readable DICOM images."],
    ["garbage", "The service sent an unexpected response."],
    ["server-error", "The service hit an internal error. Try again later."],
  ];
  const names = await upload(page, cases.map(([scenario]) => ({ scenario })));
  for (const [i, [, message]] of cases.entries()) await expect(row(page, names[i])).toContainText(message);
  await expect(page.locator("body")).not.toContainText(/Traceback|Exception|undefined/);
});

test("keeps the results of concurrent uploads apart", async ({ page }) => {
  await openApp(page);
  const [two, spleen] = await upload(page, [{ scenario: "slow-two" }, { scenario: "fast-spleen" }]);
  await expect(row(page, spleen)).toContainText("Done");
  await expect(row(page, two)).toContainText("Done");

  await row(page, spleen).getByRole("button", { name: /^Show / }).click();
  await expect(page.getByRole("switch", { name: "Model: spleen" })).toBeVisible();
  await expect(page.getByRole("switch", { name: /Model: (lesions|liver)/ })).toHaveCount(0);
  await expect(page.locator(".compare")).toContainText("No lesion mask in this result.");

  await row(page, two).getByRole("button", { name: /^Show / }).click();
  await expect(page.getByRole("switch", { name: "Model: lesions" })).toBeVisible();
  await expect(page.getByRole("switch", { name: "Model: liver" })).toBeVisible();
  await expect(page.getByRole("switch", { name: "Model: spleen" })).toHaveCount(0);
  await expect(page.locator(".dsc")).toHaveText(/^0\.\d{3}$/);
});

test("never has more than two uploads in flight; extra ones wait their turn", async ({ page, request }) => {
  await openApp(page);
  const names = await upload(page, [1, 2, 3, 4].map(() => ({ scenario: "slow" })));
  await expect(page.getByText("Waiting for a free upload slot").first()).toBeVisible();
  for (const name of names) await expect(row(page, name)).toContainText("Done", { timeout: 30_000 });
  const stats = await (await request.get(`${FAKE_API}/__stats`)).json();
  expect(stats.peak_in_flight).toBeLessThanOrEqual(2);
});

test("waits out Retry-After when the service is busy, then retries", async ({ page }) => {
  await openApp(page);
  const [name] = await upload(page, [{ scenario: "busy" }]);
  await expect(row(page, name)).toContainText(/Service busy\. Retrying in \d s/);
  await expect(row(page, name)).toContainText("Done");
});

test("cancels an upload that is still running", async ({ page }) => {
  await openApp(page);
  const [name] = await upload(page, [{ scenario: "hang" }]);
  await expect(row(page, name)).toContainText("Segmenting");
  await row(page, name).getByRole("button", { name: /Cancel upload/ }).click();
  await expect(row(page, name)).toContainText("Cancelled");
  await expect(row(page, name)).toContainText("Upload cancelled.");
  await expect(row(page, name).getByRole("button", { name: /Cancel upload/ })).toHaveCount(0);
});

test("clears failed uploads and keeps the results", async ({ page }) => {
  await openApp(page);
  const [good, bad] = await upload(page, [{ scenario: "slow" }, { scenario: "not-dicom" }]);
  await expect(row(page, bad)).toContainText("Failed");
  await expect(row(page, good)).toContainText("Done");
  await page.getByRole("button", { name: "Clear failed" }).click();
  await expect(row(page, bad)).toHaveCount(0);
  await expect(page.locator(".run.failed, .run.cancelled")).toHaveCount(0);
  await expect(row(page, good)).toBeVisible();
  await expect(page.getByRole("button", { name: "Clear failed" })).toHaveCount(0);
});

test("refuses a mask that isn't on the CT grid instead of drawing it in the wrong place", async ({ page }) => {
  await openApp(page);
  const [name] = await upload(page, [{ scenario: "other-grid" }]);
  await expect(row(page, name)).toContainText("Done");
  await row(page, name).getByRole("button", { name: /^Show / }).click();
  await expect(page.getByText("This mask doesn't line up with the CT grid, so it can't be overlaid.")).toBeVisible();
  await expect(page.getByRole("switch", { name: "Model: lesions" })).toBeDisabled();
  await expect(page.locator(".compare")).toContainText("The masks are positioned differently in space.");
});

test("warns when the service matches the upload to a different case", async ({ page }) => {
  await openApp(page);
  const [name] = await upload(page, [{ scenario: "wrong-case" }]);
  await expect(row(page, name)).toContainText("identified this upload as case-999");
});

test("refuses a file over 150 MB without sending it", async ({ page }) => {
  await openApp(page);
  const dir = mkdtempSync(join(tmpdir(), "viewer-e2e-"));
  const big = join(dir, "huge-scan.zip");
  writeFileSync(big, Buffer.alloc(157_286_401));
  try {
    await page.getByLabel("Choose zip files to upload").setInputFiles(big);
    await expect(page.getByRole("alert").filter({ hasText: "huge-scan.zip is larger than the 150 MB" })).toBeVisible();
    await page.getByRole("button", { name: "Dismiss" }).click();
    await expect(page.getByText("huge-scan.zip is larger than")).toHaveCount(0);
    await expect(row(page, "huge-scan.zip")).toHaveCount(0);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test("uploads files dropped onto the page", async ({ page }) => {
  await openApp(page);
  const name = unique("dropped");
  await page.evaluate((fileName) => {
    const dt = new DataTransfer();
    dt.items.add(new File([`SCENARIO:not-dicom:${fileName}`], fileName, { type: "application/zip" }));
    window.dispatchEvent(new DragEvent("dragenter", { dataTransfer: dt, bubbles: true, cancelable: true }));
    window.dispatchEvent(new DragEvent("dragover", { dataTransfer: dt, bubbles: true, cancelable: true }));
    window.dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true }));
  }, name);
  await expect(row(page, name)).toContainText("The zip contains files that aren't readable DICOM images.");
});

test("says so when the local server drops out, and recovers by itself", async ({ page }) => {
  await openApp(page);
  await page.route("**/api/**", (route) => route.abort());
  await expect(page.getByRole("alert").filter({ hasText: "Lost connection to the local server" })).toBeVisible();
  await page.unroute("**/api/**");
  await expect(page.getByText("Lost connection to the local server")).toHaveCount(0);
});

test("keeps the token out of the browser", async ({ page }) => {
  const bodies: string[] = [];
  page.on("response", async (r) => {
    if (r.url().includes("/api/") && !r.url().includes("volume") && !r.url().includes("mask") && !r.url().includes("structures")) {
      bodies.push(await r.text().catch(() => ""));
    }
  });
  await openApp(page);
  await page.getByRole("button", { name: "Upload case-001" }).click();
  await expect(page.locator(".run").first()).toContainText("Done", { timeout: 30_000 });
  expect(bodies.length).toBeGreaterThan(2);
  for (const b of bodies) expect(b).not.toContain("e2e-token");
  expect(await page.content()).not.toContain("e2e-token");
});

test("has no serious accessibility problems", async ({ page }) => {
  await openApp(page);
  await page.getByRole("button", { name: "Upload case-001" }).click();
  await expect(page.locator(".dsc")).toBeVisible({ timeout: 30_000 });
  const { violations } = await new AxeBuilder({ page }).analyze();
  const serious = violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
});

test("works on a phone-sized screen", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openApp(page);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await expect(page.getByRole("button", { name: "Upload case-001" })).toBeVisible();
});
