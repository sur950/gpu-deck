import { Cloud, CloudUpload } from 'lucide-react';
import type { NavEntry } from '@/lib/nav/types';

/** Nav entry: cloud GPU VM provisioning. */
export const cloudNav: NavEntry[] = [
  {
    id: 'cloud',
    title: 'Cloud VMs',
    href: '/cloud',
    icon: Cloud,
    group: 'fleet',
    order: 15,
    description: 'GPU VMs on AWS, Azure, GCP and DigitalOcean that install the agent and join a cloud cluster.',
    keywords: ['cloud', 'aws', 'ec2', 'azure', 'gcp', 'digitalocean', 'vm', 'provision', 'instance type'],
    breadcrumbs: { new: 'Provision a GPU VM' },
    detailLabel: 'VM details',
    actions: [{ label: 'Provision a cloud GPU VM', href: '/cloud/new', icon: CloudUpload, keywords: ['aws', 'azure', 'gcp', 'digitalocean', 'provision'] }],
  },
];
