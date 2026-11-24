/**
 * Org-level theme/branding shape.
 * Used by ThemeProvider and utils/theme.ts.
 */

export interface Theme {
  orgName: string;
  logo: string;
  primaryColor: string;
  secondaryColor?: string;
  tertiaryColor?: string;
  sidebarStyle: 'dark' | 'light';
  whiteLabel: boolean;
  colors: {
    success: { light: string; default: string; dark: string };
    warning: { light: string; default: string; dark: string };
    error:   { light: string; default: string; dark: string };
    info:    { light: string; default: string; dark: string };
    neutral: {
      50: string; 100: string; 200: string; 300: string; 400: string;
      500: string; 600: string; 700: string; 800: string; 900: string;
    };
  };
  typography?: {
    fontFamily: { sans: string; mono: string };
    fontSize: {
      xs: string; sm: string; base: string; lg: string;
      xl: string; '2xl': string; '3xl': string; '4xl': string;
    };
    fontWeight: { normal: string; medium: string; semibold: string; bold: string };
  };
  borderRadius?: { sm: string; md: string; lg: string; xl: string };
}
