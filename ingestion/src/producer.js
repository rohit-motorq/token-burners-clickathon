const { Kafka, CompressionTypes, logLevel } = require('@confluentinc/kafka-javascript').KafkaJS;

function createProducer({ broker, token, clientId }) {
  // Any username works — StreamNative authenticates on the token alone.
  const kafka = new Kafka({
    kafkaJS: {
      clientId,
      brokers: [broker],
      ssl: true,
      sasl: { mechanism: 'plain', username: 'user', password: `token:${token}` },
      logLevel: logLevel.ERROR,
    },
  });

  return kafka.producer({
    kafkaJS: { acks: -1, compression: CompressionTypes.LZ4 },
  });
}

// Prints one message per batch instead of producing, so a new CSV can be
// verified end to end without credentials.
function createDryRunProducer() {
  return {
    connect: async () => {},
    disconnect: async () => {},
    send: async ({ topic, messages }) => {
      for (const message of messages) {
        console.log(`[dry-run] ${topic} key=${message.key ?? '<none>'} ${message.value}`);
      }
    },
  };
}

module.exports = { createProducer, createDryRunProducer };
