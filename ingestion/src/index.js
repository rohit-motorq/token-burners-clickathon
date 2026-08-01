const { loadConfig, USAGE } = require('./config');
const { readRows } = require('./csvSource');
const { createProducer, createDryRunProducer } = require('./producer');

function toMessage(row, keyField) {
  const key = keyField && row[keyField] ? String(row[keyField]) : null;
  return { key, value: JSON.stringify(row) };
}

async function run(config) {
  const producer = config.dryRun ? createDryRunProducer() : createProducer(config);
  await producer.connect();

  const startedAt = Date.now();
  let sent = 0;
  let batch = [];

  const flush = async () => {
    if (batch.length === 0) return;
    await producer.send({ topic: config.topic, messages: batch });
    sent += batch.length;
    batch = [];
    const elapsed = (Date.now() - startedAt) / 1000;
    console.log(`sent ${sent} rows (${Math.round(sent / elapsed)} rows/s)`);
  };

  try {
    for await (const row of readRows(config.csvPath, { delimiter: config.delimiter })) {
      batch.push(toMessage(row, config.keyField));
      if (batch.length >= config.batchSize) await flush();
      if (config.limit && sent + batch.length >= config.limit) break;
    }
    await flush();
  } finally {
    await producer.disconnect();
  }

  const elapsed = (Date.now() - startedAt) / 1000;
  console.log(`done: ${sent} rows -> ${config.topic} in ${elapsed.toFixed(1)}s`);
}

async function main() {
  const config = loadConfig();
  if (config.help) {
    console.log(USAGE);
    return;
  }
  console.log(
    `ingesting ${config.csvPath} -> ${config.topic}` +
      `${config.keyField ? ` (key: ${config.keyField})` : ''}${config.dryRun ? ' [dry-run]' : ''}`,
  );
  await run(config);
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
