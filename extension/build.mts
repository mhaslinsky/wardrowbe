#!/usr/bin/env node
import { createRequire } from 'node:module';
import { mkdir, readFile, writeFile, copyFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
const require = createRequire(new URL('./package.json', import.meta.url));
const root = resolve(import.meta.dirname, '..');
try {
  const { build } = require('esbuild');
  for (const target of ['chrome', 'firefox']) {
    const outdir = `${root}/extension/dist/${target}`;
    await mkdir(outdir, { recursive: true });
    await build({ entryPoints: ['background', 'bridge', 'options', 'error'].map(name => `${root}/extension/${name}.ts`), outdir, bundle: true, tsconfig: `${root}/frontend/tsconfig.json`, format: 'iife', target: 'es2022', minify: false });
    await copyFile(`${root}/extension/manifest.${target}.json`, `${outdir}/manifest.json`);
    await copyFile(`${root}/extension/options.html`, `${outdir}/options.html`);
    await copyFile(`${root}/extension/error.html`, `${outdir}/error.html`);
    for (const locale of ['en', 'de', 'fr', 'it', 'ja', 'ko', 'zh-CN', 'zh-TW']) {
      const messages = JSON.parse(await readFile(`${root}/frontend/messages/${locale}/wardrobe.json`, 'utf8')).import;
      if (!messages) throw Error(`Missing import messages: ${locale}`);
      const directory = `${outdir}/_locales/${locale.replace('-', '_')}`;
      await mkdir(directory, { recursive: true });
      await writeFile(`${directory}/messages.json`, JSON.stringify(Object.fromEntries(Object.entries(messages).map(([key, value]) => [key, { message: value }]))));
    }
    const archive = `${root}/extension/dist/wardrowbe-${target}.zip`;
    const result = spawnSync('zip', ['-q', '-r', '-FS', archive, '.'], { cwd: outdir, stdio: 'inherit' });
    if (result.status !== 0) throw Error(`Packaging failed: ${target}`);
    console.log(`Built ${target}: ${outdir}`);
  }
} catch (error) { process.exitCode = 1; console.error(error instanceof Error ? error.message : 'Build failed'); }
