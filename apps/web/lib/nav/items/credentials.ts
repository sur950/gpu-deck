import { Vault } from 'lucide-react';
import type { NavEntry } from '@/lib/nav/types';

/** Nav entry: Credentials / vault. */
export const credentialsNav: NavEntry[] = [
  {
    id: 'credentials',
    title: 'Credentials',
    href: '/credentials',
    icon: Vault,
    group: 'security',
    order: 15,
    description: 'Cloud keys, model-hub tokens, alert secrets and SSH keys in the encrypted vault. Pick them wherever they are needed.',
    keywords: ['vault', 'secret', 'credential', 'token', 'aws', 'azure', 'gcp', 'hugging face', 'slack', 'smtp', 'ssh key'],
    detailLabel: 'Credential',
    actions: [
      { label: 'Add credential', href: '/credentials?add=any', keywords: ['vault', 'secret', 'new'] },
      { label: 'Add AWS access key', href: '/credentials?add=aws', keywords: ['cloud', 'credential'] },
      { label: 'Add Hugging Face token', href: '/credentials?add=huggingface', keywords: ['hf', 'token'] },
    ],
  },
];
