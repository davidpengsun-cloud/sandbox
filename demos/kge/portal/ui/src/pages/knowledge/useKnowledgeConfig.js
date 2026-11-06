import { useConfig } from "../../services/config";
import { normaliseConfig } from "./knowledgeConfig";

// The one place the Knowledge pages read the portal's useConfig hook (GET /api/config).
export default function useKnowledgeConfig() {
  return normaliseConfig(useConfig());
}
