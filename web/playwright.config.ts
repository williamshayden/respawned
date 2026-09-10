import { defineConfig, devices } from '@playwright/test'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const liveUrl = process.env.RESPAWNED_LIVE_UI_URL

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  workers: 2,
  timeout: 30_000,
  expect: { timeout: 7_000 },
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: 'list',
  outputDir: process.env.RESPAWNED_TEST_OUTPUT_DIR || join(tmpdir(), 'respawned-playwright'),
  use: {
    baseURL: liveUrl || 'http://127.0.0.1:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 1000 } } }],
  webServer: liveUrl ? undefined : {
    command: 'npm run dev',
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
})
