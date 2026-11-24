import type { Metadata } from 'next';
import { GpuInstanceDetail } from '@/app/(dashboard)/gpu-instances/_components/gpu-instance-detail';

export const metadata: Metadata = { title: 'SSH instance' };

export default function GpuInstanceDetailPage() {
  return <GpuInstanceDetail />;
}
