import os from 'node:os';
import { defineConfig, devices } from '@playwright/test';

const defaultWorkers = Math.max(1, Math.min(4, Math.floor(os.cpus().length / 2)));

export default defineConfig({
  testDir: '.',
  testMatch: /.*_test\.ts$/,
  testIgnore: ['**/node_modules/**', '**/.venv/**', '**/dist/**', '**/static/**'],
  fullyParallel: true,
  workers: defaultWorkers,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: 'list',
  use: {
    baseURL: 'https://canvas.test',
    headless: true,
    serviceWorkers: 'block',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
      },
    },
  ],
});
