const path = require('node:path');
const fs = require('node:fs');
const { parseArgs } = require('node:util');

const PACKAGE_ROOT = path.resolve(__dirname, '..');

function loadDotEnv() {
  const envPath = path.join(PACKAGE_ROOT, '.env');
  if (fs.existsSync(envPath)) {
    process.loadEnvFile(envPath);
  }
}

function positiveInt(value, name) {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    throw new Error(`${name} must be a positive integer, got "${value}"`);
  }
  return parsed;
}

function loadConfig(argv = process.argv.slice(2)) {
  loadDotEnv();

  const { values } = parseArgs({
    args: argv,
    options: {
      csv: { type: 'string' },
      topic: { type: 'string' },
      key: { type: 'string' },
      delimiter: { type: 'string' },
      batch: { type: 'string' },
      limit: { type: 'string' },
      'dry-run': { type: 'boolean', default: false },
      help: { type: 'boolean', default: false },
    },
  });

  if (values.help) return { help: true };

  const csvPath = values.csv ?? process.env.CSV_PATH;
  const topic = values.topic ?? process.env.KAFKA_TOPIC;
  const dryRun = values['dry-run'];

  const missing = [];
  if (!csvPath) missing.push('--csv / CSV_PATH');
  if (!topic) missing.push('--topic / KAFKA_TOPIC');
  if (!dryRun && !process.env.STREAMNATIVE_BROKER) missing.push('STREAMNATIVE_BROKER');
  if (!dryRun && !process.env.STREAMNATIVE_TOKEN) missing.push('STREAMNATIVE_TOKEN');
  if (missing.length > 0) {
    throw new Error(`Missing required configuration: ${missing.join(', ')}`);
  }

  return {
    help: false,
    dryRun,
    csvPath: path.resolve(PACKAGE_ROOT, csvPath),
    delimiter: values.delimiter ?? process.env.CSV_DELIMITER ?? ',',
    keyField: values.key ?? process.env.KEY_FIELD ?? null,
    batchSize: positiveInt(values.batch ?? process.env.BATCH_SIZE ?? '5000', 'batch size'),
    limit: values.limit ? positiveInt(values.limit, 'limit') : null,
    topic,
    broker: process.env.STREAMNATIVE_BROKER,
    token: process.env.STREAMNATIVE_TOKEN,
    clientId: process.env.CLIENT_ID ?? 'csv-ingestion',
  };
}

const USAGE = `
Stream any CSV file into a StreamNative (Kafka protocol) topic.

  node src/index.js [options]

Options:
  --csv <path>        CSV file to ingest            (env CSV_PATH)
  --topic <name>      Target topic                  (env KAFKA_TOPIC)
  --key <column>      Column used as message key    (env KEY_FIELD)
  --delimiter <char>  Field delimiter, default ","  (env CSV_DELIMITER)
  --batch <n>         Rows per produce batch        (env BATCH_SIZE, default 5000)
  --limit <n>         Stop after n rows (smoke tests)
  --dry-run           Print messages instead of producing; no broker needed
  --help              Show this message

Broker credentials come from .env: STREAMNATIVE_BROKER, STREAMNATIVE_TOKEN.
`;

module.exports = { loadConfig, USAGE };
