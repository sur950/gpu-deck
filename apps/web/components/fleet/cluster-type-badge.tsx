import { Box, Cloud, Container, Server } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { CLUSTER_TYPE_LABELS, type ClusterType } from '@/types/fleet';

export const CLUSTER_TYPE_ICONS: Record<ClusterType, LucideIcon> = {
  bare_metal: Server,
  vm: Box,
  kubernetes: Container,
  cloud_aws: Cloud,
  cloud_azure: Cloud,
  cloud_gcp: Cloud,
  cloud_do: Cloud,
};

export function ClusterTypeBadge({ type, className }: { type: ClusterType; className?: string }) {
  const Icon = CLUSTER_TYPE_ICONS[type];
  return (
    <Badge variant="outline" className={cn('gap-1 font-normal', className)}>
      <Icon className="size-3 text-muted-foreground" />
      {CLUSTER_TYPE_LABELS[type]}
    </Badge>
  );
}
