/**
 * Per-deployment chart labels (Observability › Inference): an observed (external) deployment is
 * shown by name, else its served model name, else a short id — never a bare UUID.
 */
import { describe, expect, it } from 'vitest';
import { deploymentEntities, deploymentLabel, looksLikeUuid, shortId } from '@/utils/deployment-label';

const EXT = '0cf33809-50c4-5e86-82f1-72d83ff6671f';
const GONE = '9f66d8fd-8f6e-566f-8ba0-45a12b0343e3';

describe('deploymentLabel', () => {
  it('prefers the deployment name', () => {
    expect(deploymentLabel(EXT, { id: EXT, name: 'main@gpu-node', model_name: 'main' })).toBe('main@gpu-node');
  });

  it('skips a name that is only the id and falls back to the served model', () => {
    expect(deploymentLabel(EXT, { id: EXT, name: EXT, model_name: 'qwen-7b' })).toBe('qwen-7b');
    expect(deploymentLabel(EXT, { id: EXT, name: EXT }, { served_model: 'llama-3' })).toBe('llama-3');
  });

  it('uses the series labels when the deployment is not in the list', () => {
    expect(deploymentLabel(EXT, undefined, { deployment: 'emb@gpu-node' })).toBe('emb@gpu-node');
    expect(deploymentLabel(EXT, null, { model: 'bge-m3' })).toBe('bge-m3');
  });

  it('ends with a short id', () => {
    expect(deploymentLabel(EXT)).toBe(`deployment ${shortId(EXT)}`);
    expect(deploymentLabel(EXT)).not.toContain(EXT);
    expect(looksLikeUuid(deploymentLabel(EXT))).toBe(false);
  });
});

describe('deploymentEntities', () => {
  it('adds series-only deployments, labelled from their series, sorted by label', () => {
    const entities = deploymentEntities(
      [{ id: EXT, name: 'main@gpu-node' }],
      [
        { labels: { deployment_id: EXT, instance_id: 'i-1' } },
        { labels: { deployment_id: GONE, instance_id: 'i-2', served_model: 'emb' } },
      ],
    );
    expect(entities).toEqual([
      { id: GONE, label: 'emb' },
      { id: EXT, label: 'main@gpu-node' },
    ]);
  });

  it('applies the scope filter', () => {
    const entities = deploymentEntities([{ id: EXT, name: 'a' }], [{ labels: { deployment_id: GONE } }], (id) => id === GONE);
    expect(entities).toEqual([{ id: GONE, label: `deployment ${shortId(GONE)}` }]);
  });
});
