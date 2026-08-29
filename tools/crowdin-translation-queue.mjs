#!/usr/bin/env node

/**
 * Poll queued source strings in Crowdin and optionally submit a translation.
 *
 * Required environment variables:
 *   CROWDIN_PROJECT_ID  The numeric project ID.
 *   CROWDIN_TOKEN       A personal access token with source-string read and
 *                       translation-create permission for that project.
 *
 * Examples:
 *   node tools/crowdin-translation-queue.mjs
 *   node tools/crowdin-translation-queue.mjs --watch 900 --submit
 *   node tools/crowdin-translation-queue.mjs --list-languages
 *
 * Entries are not submitted unless --submit is present. Successful submissions
 * are recorded locally in tmp/crowdin-translation-queue-state.json so they are
 * not proposed again. Crowdin determines whether a submitted translation is
 * immediately approved or remains a community suggestion.
 */

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import process from "node:process";

const defaultQueuePath = "tools/crowdin-translation-queue.json";
const defaultStatePath = "tmp/crowdin-translation-queue-state.json";
const apiBase = "https://api.crowdin.com/api/v2";

function usage() {
  console.log(`Usage: node tools/crowdin-translation-queue.mjs [options]

Options:
  --queue <path>       Queue JSON file (default: ${defaultQueuePath})
  --state <path>       Local submission state (default: ${defaultStatePath})
  --submit             Submit a matching queued translation
  --watch <seconds>    Poll repeatedly; requires a positive interval
  --list-languages     Print enabled Crowdin target-language IDs and exit
  --help               Show this help
`);
}

function parseArgs(args) {
  const options = {
    queuePath: defaultQueuePath,
    statePath: defaultStatePath,
    submit: false,
    watchSeconds: null,
    listLanguages: false,
  };

  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === "--help") return { help: true };
    if (arg === "--submit") {
      options.submit = true;
      continue;
    }
    if (arg === "--list-languages") {
      options.listLanguages = true;
      continue;
    }
    if (arg === "--queue" || arg === "--state" || arg === "--watch") {
      const value = args[index + 1];
      if (!value) throw new Error(`${arg} requires a value`);
      index += 1;
      if (arg === "--queue") options.queuePath = value;
      if (arg === "--state") options.statePath = value;
      if (arg === "--watch") {
        options.watchSeconds = Number(value);
        if (!Number.isFinite(options.watchSeconds) || options.watchSeconds <= 0) {
          throw new Error("--watch must be a positive number of seconds");
        }
      }
      continue;
    }
    throw new Error(`Unknown option: ${arg}`);
  }
  return options;
}

async function readJson(path, fallback) {
  try {
    return JSON.parse(await readFile(path, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") return fallback;
    throw error;
  }
}

async function writeJson(path, value) {
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, `${JSON.stringify(value, null, 2)}\n`);
}

function requiredEnvironment(name) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} must be set`);
  return value;
}

function createClient() {
  const projectId = requiredEnvironment("CROWDIN_PROJECT_ID");
  const token = requiredEnvironment("CROWDIN_TOKEN");

  async function request(path, options = {}) {
    const response = await fetch(`${apiBase}/projects/${projectId}${path}`, {
      ...options,
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${token}`,
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...options.headers,
      },
    });
    if (!response.ok) {
      throw new Error(`Crowdin API ${response.status}: ${await response.text()}`);
    }
    return response.json();
  }

  return {
    listLanguages: () => request("/languages?limit=500"),
    findStrings: (source) =>
      request(
        `/strings?${new URLSearchParams({
          filter: source,
          scope: "text",
          limit: "100",
        })}`
      ),
    addTranslation: (stringId, languageId, text) =>
      request("/translations", {
        method: "POST",
        body: JSON.stringify({ stringId, languageId, text, addToTm: false }),
      }),
  };
}

function entryKey(entry) {
  return `${entry.languageId}\u0000${entry.source}\u0000${entry.context || ""}`;
}

function findExactString(response, entry) {
  const matches = (response.data || [])
    .map((item) => item.data)
    .filter((string) => string.text === entry.source)
    .filter((string) => !entry.context || string.context === entry.context);

  if (matches.length > 1) {
    throw new Error(`More than one Crowdin source string matches: ${entry.source}`);
  }
  return matches[0];
}

async function processQueue(options, client, queue, state, statePath) {
  for (const entry of queue.entries) {
    for (const field of ["source", "languageId", "translation"]) {
      if (!entry[field]) throw new Error(`Queue entry is missing ${field}`);
    }

    const key = entryKey(entry);
    if (state.submitted[key]) {
      console.log(`already submitted: ${entry.source} [${entry.languageId}]`);
      continue;
    }

    const sourceString = findExactString(await client.findStrings(entry.source), entry);
    if (!sourceString) {
      console.log(`waiting for Crowdin source: ${entry.source}`);
      continue;
    }

    if (!options.submit) {
      console.log(`ready to submit: ${entry.source} [${entry.languageId}]`);
      continue;
    }

    const submission = await client.addTranslation(
      sourceString.id,
      entry.languageId,
      entry.translation
    );
    state.submitted[key] = {
      at: new Date().toISOString(),
      crowdinStringId: sourceString.id,
      crowdinTranslationId: submission.data?.id ?? null,
    };
    await writeJson(statePath, state);
    console.log(`submitted: ${entry.source} [${entry.languageId}]`);
  }
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) return usage();

  const client = createClient();
  if (options.listLanguages) {
    const languages = await client.listLanguages();
    for (const { data } of languages.data || []) {
      console.log(`${data.id}\t${data.name}\t${data.twoLettersCode || ""}`);
    }
    return;
  }

  const queue = await readJson(resolve(options.queuePath));
  if (!Array.isArray(queue.entries)) throw new Error("Queue must contain an entries array");
  const statePath = resolve(options.statePath);
  const state = await readJson(statePath, { submitted: {} });

  do {
    await processQueue(options, client, queue, state, statePath);
    if (options.watchSeconds) {
      await new Promise((resolveSleep) => setTimeout(resolveSleep, options.watchSeconds * 1000));
    }
  } while (options.watchSeconds);
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
