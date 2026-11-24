import type { Metadata } from 'next';
import { GpuDetail } from '@/app/(dashboard)/gpus/_components/gpu-detail';

export const metadata: Metadata = { title: 'GPU details' };

export default function GpuDetailPage() {
  return <GpuDetail />;
}
