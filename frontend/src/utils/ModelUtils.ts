import { Model } from '../@types/conversation';
import { ModelItem } from '../@types/global-config';

export const resolveDefaultModel = (
  candidates: (Model | undefined)[],
  filteredModels: ModelItem[]
): Model | undefined => {
  for (const candidate of candidates) {
    if (candidate && filteredModels.some((m) => m.modelId === candidate)) {
      return candidate;
    }
  }
  return filteredModels[0]?.modelId;
};
