import type { Metadata } from 'next';
import { SwapDetail } from '@/app/(dashboard)/swaps/_components/swap-detail';

export const metadata: Metadata = { title: 'Swap details' };

export default function SwapDetailPage() {
  return <SwapDetail />;
}
