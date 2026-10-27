// One-shot, idempotent local replica-set initialization. Never removes data.
try {
  const status = rs.status();
  if (status.set !== "rs0") throw new Error("Unexpected replica-set name");
} catch (error) {
  if (error.code !== 94 && error.codeName !== "NotYetInitialized") throw error;
  rs.initiate({_id: "rs0", members: [{_id: 0, host: "mongo:27017"}]});
}
for (let attempt = 0; attempt < 60; attempt++) {
  if (db.hello().isWritablePrimary) {
    print("Local replica set rs0 is writable");
    quit(0);
  }
  sleep(500);
}
throw new Error("Timed out waiting for local primary");
