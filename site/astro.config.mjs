import { defineConfig } from 'astro/config';

// Static output only. Nothing renders on request — every page the site serves
// is a file, and every number on it was computed by the pipeline before deploy.
export default defineConfig({
  output: 'static',
  build: { format: 'directory' },
  server: { port: 4321 },
});
