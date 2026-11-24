import type { AutoPickResult, CompatCheck, Deployment, Engine, EngineVersion, Model, ModelVersion, PlacementPreview } from '@/types/models';
import type { Cluster, Device, Node } from '@/types/fleet';
import type { EngineCaps, WizardDraft } from '@/components/models/wizard/draft';

/** Everything the steps read besides the draft itself. */
export interface WizardContext {
  mode: 'create' | 'edit';
  current: Deployment | null;
  models: Model[];
  model: Model | undefined;
  versions: ModelVersion[];
  version: ModelVersion | undefined;
  engines: Engine[];
  /** Engine version the deployment will run on (chosen, or the auto-pick). */
  engineVersion: EngineVersion | undefined;
  engine: Engine | undefined;
  caps: EngineCaps;
  autoPick: AutoPickResult | undefined;
  autoPickLoading: boolean;
  compat: CompatCheck[];
  compatLoading: boolean;
  nodes: Node[];
  devices: Device[];
  clusters: Cluster[];
  preview: PlacementPreview | undefined;
  previewLoading: boolean;
  takenNames: Set<string>;
}

export interface StepProps {
  draft: WizardDraft;
  update: (patch: Partial<WizardDraft>) => void;
  ctx: WizardContext;
}
