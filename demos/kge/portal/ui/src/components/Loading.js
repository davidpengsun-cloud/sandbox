import React from "react";
import { Typography } from "@material-ui/core";
import { knownConfig, shortNameOf } from "../services/config";

export default function Loading() {
  return <Typography style={{ margin: "4rem" }}>{`Loading ${shortNameOf(knownConfig())}…`}</Typography>;
}
