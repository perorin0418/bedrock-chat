import { describe, expect, it } from 'vitest';
import { resolveDefaultModel } from '../ModelUtils';
import { ModelItem } from '../../@types/global-config';
import { Model } from '../../@types/conversation';

const modelItem = (modelId: Model): ModelItem => ({
  modelId,
  label: modelId,
  supportMediaType: [],
  supportReasoning: false,
});

describe('resolveDefaultModel', () => {
  const filteredModels = [
    modelItem('claude-v3.5-sonnet'),
    modelItem('amazon-nova-lite'),
  ];

  it('returns the first candidate that is in filteredModels', () => {
    expect(
      resolveDefaultModel(
        ['amazon-nova-lite', 'claude-v3.5-sonnet'],
        filteredModels
      )
    ).toBe('amazon-nova-lite');
  });

  it('falls through to the next candidate when the first is not available', () => {
    expect(
      resolveDefaultModel(['claude-v3-opus', 'claude-v3.5-sonnet'], filteredModels)
    ).toBe('claude-v3.5-sonnet');
  });

  it('falls back to the first filtered model when no candidate is available', () => {
    expect(resolveDefaultModel(['claude-v3-opus'], filteredModels)).toBe(
      'claude-v3.5-sonnet'
    );
  });

  it('returns undefined when there are no filtered models and no candidates', () => {
    expect(resolveDefaultModel([], [])).toBeUndefined();
  });
});
