/**
 * Side-effect module: `import '@/lib/mocks/boot'` from a client layout turns
 * mock mode on when NEXT_PUBLIC_API_MOCKS=1 (no-op otherwise).
 */

import { bootMocks } from '@/lib/mocks';

bootMocks();
