import { Library } from 'lucide-react';
import type { NavEntry } from '@/lib/nav/types';

/** Nav entry: catalog. */
export const catalogNav: NavEntry[] = [
  {
    id: 'catalog',
    title: 'Catalog',
    href: '/catalog',
    icon: Library,
    group: 'models',
    order: 10,
    description: 'Models from Hugging Face, ModelScope and local paths, with versions and priority.',
    keywords: ['model', 'hugging face', 'modelscope', 'gguf', 'safetensors'],
    breadcrumbs: { engines: 'Engines', new: 'Add a model' },
    detailLabel: 'Model details',
    actions: [
      { label: 'Add a model', href: '/catalog/new', keywords: ['register', 'hugging face', 'modelscope', 'ollama', 'lm studio', 'local'] },
      { label: 'Engine registry', href: '/catalog/engines', keywords: ['engine', 'llama.cpp', 'vllm', 'sglang', 'mlx', 'custom'] },
    ],
  },
];
