import { createApp } from "./app.js";

const port = Number(process.env.PORT || 8080);
let app;
try {
  ({ app } = createApp());
} catch (error) {
  console.error(`Portal cannot start: ${error.message}`);
  process.exit(1);
}
app.listen(port, "0.0.0.0", (error) => {
  if (error) {
    console.error(`Portal cannot listen on ${port}: ${error.code || error.message}`);
    process.exit(1);
  }
  console.log(`Portal listening on ${port}`);
});
