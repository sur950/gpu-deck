import { cn } from '@/lib/utils';

/** Page width cap and padding used by every signed-in page. */
export const PAGE_CLASS = 'mx-auto w-full max-w-[1440px] space-y-6 p-4 sm:p-6 lg:p-8';

interface PageContainerProps {
  children: React.ReactNode;
  className?: string;
}

export function PageContainer({ children, className }: PageContainerProps) {
  return <div className={cn(PAGE_CLASS, className)}>{children}</div>;
}
