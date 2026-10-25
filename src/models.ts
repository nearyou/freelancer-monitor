export interface Employer {
  country: string;
  payment_verified: boolean;
  deposit_made: boolean;
  profile_completed: boolean;
  rating: number;
  review_count: number;
  member_since: Date | null;
  identity_verified: boolean | null;
  phone_verified: boolean | null;
  email_verified: boolean | null;
}

export interface Project {
  id: number;
  title: string;
  description: string;
  url: string;
  owner_id: number | null;
  skills: string[];
  listing_types: string[];
  currency_code: string;
  budget_min: number | null;
  budget_max: number | null;
  hourly: boolean;
  submitted_at: Date | null;
  employer: Employer;
  employer_data_complete: boolean;
}

export const defaultEmployer = (): Employer => ({
  country: '', payment_verified: false, deposit_made: false, profile_completed: false,
  rating: 0, review_count: 0, member_since: null,
  identity_verified: null, phone_verified: null, email_verified: null,
});

// Keep common Python casefold mappings used by existing filter_terms rows.
export const fold = (value: string): string => value.toLowerCase().replaceAll('ß', 'ss').replaceAll('ς', 'σ');
export const escapeHtml = (value: string): string => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
