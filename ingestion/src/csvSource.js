const fs = require('node:fs');
const { parse } = require('csv-parse');

const LFS_POINTER_PREFIX = 'version https://git-lfs.github.com/spec/v1';

function assertRealFile(csvPath) {
  const buffer = Buffer.alloc(LFS_POINTER_PREFIX.length);
  const fd = fs.openSync(csvPath, 'r');
  try {
    fs.readSync(fd, buffer, 0, buffer.length, 0);
  } finally {
    fs.closeSync(fd);
  }
  if (buffer.toString('utf8') === LFS_POINTER_PREFIX) {
    throw new Error(
      `${csvPath} is a Git LFS pointer, not the actual CSV. Run "git lfs pull" first.`,
    );
  }
}

// Yields one plain object per data row, keys taken from the header line,
// so the pipeline never needs to know the schema in advance.
function readRows(csvPath, { delimiter = ',' } = {}) {
  assertRealFile(csvPath);

  return fs.createReadStream(csvPath).pipe(
    parse({
      delimiter,
      columns: (header) => header.map((name) => name.trim()),
      bom: true,
      skip_empty_lines: true,
      trim: true,
      relax_column_count: true,
    }),
  );
}

module.exports = { readRows };
