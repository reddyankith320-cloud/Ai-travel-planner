/**
 * Types mirroring the backend's Pydantic schemas.
 *
 * Kept hand-written rather than generated so the shapes the UI actually
 * depends on are visible in one place. The backend's OpenAPI document at
 * `/openapi.json` is the source of truth if these ever drift.
 */

export type TravelMode = 'bus' | 'train' | 'car' | 'flight' | 'any';

export type AccommodationType =
  | 'hostel'
  | 'budget_hotel'
  | 'midrange_hotel'
  | 'homestay'
  | 'resort';

export type Pace = 'relaxed' | 'balanced' | 'packed';

/** Where a number came from. Drives every "estimated vs live" label in the UI. */
export type DataSource = 'live' | 'cached' | 'estimated' | 'curated' | 'sample';

export type Confidence = 'high' | 'medium' | 'low';

export type BudgetFit = 'comfortable' | 'good' | 'tight' | 'stretch' | 'over';

export type CostCategory =
  | 'intercity_transport'
  | 'accommodation'
  | 'food'
  | 'local_transport'
  | 'activities'
  | 'taxes_and_fees'
  | 'contingency';

export interface Coordinates {
  lat: number;
  lon: number;
}

export interface Provenance {
  source: DataSource;
  confidence: Confidence;
  provider: string | null;
  as_of: string | null;
  note: string | null;
}

export interface BookingLink {
  label: string;
  url: string;
  official: boolean;
  note: string | null;
}

export interface PlaceRef {
  name: string;
  category: string;
  coordinates: Coordinates | null;
  entry_fee_inr: number | null;
  opening_hours: string | null;
  rating: number | null;
  typical_visit_minutes: number | null;
  description: string | null;
  provenance: Provenance;
}

export interface Assumption {
  key: string;
  label: string;
  value: number;
  unit: string;
  editable: boolean;
  provenance: Provenance;
}

export interface CostLineItem {
  category: CostCategory;
  label: string;
  basis: string;
  units: number;
  unit_label: string;
  unit_price_inr: number;
  total_inr: number;
  taxable: boolean;
  tax_rate: number;
  provenance: Provenance;
  detail: string | null;
}

export interface CategoryTotal {
  category: CostCategory;
  total_inr: number;
  share_of_total: number;
  per_traveler_inr: number;
}

export interface CostBreakdown {
  currency: string;
  travelers: number;
  days: number;
  nights: number;
  line_items: CostLineItem[];
  assumptions: Assumption[];
  categories: CategoryTotal[];
  subtotal_inr: number;
  taxes_inr: number;
  contingency_inr: number;
  contingency_rate: number;
  total_inr: number;
  per_traveler_inr: number;
  per_day_inr: number;
  per_traveler_per_day_inr: number;
  budget_inr: number;
  remaining_inr: number;
  overrun_inr: number;
  budget_utilisation: number;
  budget_fit: BudgetFit;
  confidence: Confidence;
  live_data_share: number;
  freshness_note: string;
}

export interface ScoreComponent {
  key: string;
  label: string;
  score: number;
  weight: number;
  weighted: number;
  explanation: string;
}

export interface ScoreBreakdown {
  total: number;
  components: ScoreComponent[];
}

export interface TravelLeg {
  mode: TravelMode;
  distance_km: number;
  duration_hours: number;
  provenance: Provenance;
  route_note: string | null;
  transfer_note: string | null;
  booking_links: BookingLink[];
}

export interface SeasonInfo {
  in_season: boolean;
  score: number;
  label: string;
  note: string;
  provenance: Provenance;
}

export interface DestinationSummary {
  id: string;
  name: string;
  region: string;
  state: string;
  country: string;
  coordinates: Coordinates;
  blurb: string;
  tags: string[];
  image_url: string | null;
  image_attribution: string | null;
  provenance: Provenance;
}

export interface DestinationRecommendation {
  destination: DestinationSummary;
  rank: number;
  score: ScoreBreakdown;
  budget_fit: BudgetFit;
  is_stretch: boolean;
  cost: CostBreakdown;
  outbound: TravelLeg;
  inbound: TravelLeg;
  season: SeasonInfo;
  why_recommended: string[];
  highlights: PlaceRef[];
  caveats: string[];
  round_trip_hours: number;
  intercity_cost_inr: number;
  accommodation_cost_inr: number;
}

export interface RejectedDestination {
  id: string;
  name: string;
  reason_code: string;
  reason: string;
  estimated_total_inr: number | null;
  shortfall_inr: number | null;
  tags: string[];
}

export interface UnmetInterest {
  interest: string;
  cheapest_matching_name: string | null;
  cheapest_matching_total_inr: number | null;
  additional_budget_required_inr: number | null;
}

/** Reported when the budget cannot buy the kind of trip the user asked for. */
export interface InterestGap {
  message: string;
  unmet: UnmetInterest[];
  matched: string[];
  smallest_additional_budget_inr: number | null;
}

export interface BudgetShortfall {
  message: string;
  cheapest_total_inr: number;
  additional_budget_required_inr: number;
  suggested_budget_inr: number;
  alternatives: RejectedDestination[];
  levers: string[];
}

export interface ResponseMeta {
  demo_mode: boolean;
  providers_used: string[];
  provider_failures: string[];
  candidates_considered: number;
  candidates_feasible: number;
  cache_hits: number;
  elapsed_ms: number;
  currency: string;
  data_disclaimer: string;
}

export interface OriginLocation {
  coordinates?: Coordinates | null;
  label?: string | null;
  resolved_name?: string | null;
  state?: string | null;
  detection?: string;
}

export interface TripRequest {
  origin: OriginLocation;
  budget_inr: number;
  travelers: number;
  days: number;
  start_date: string;
  end_date?: string | null;
  travel_mode: TravelMode;
  accommodation: AccommodationType;
  pace: Pace;
  interests: string[];
  max_one_way_hours?: number | null;
  include_stretch_options?: boolean;
  contingency_rate?: number | null;
  assumption_overrides?: Record<string, number>;
}

export interface RecommendationResponse {
  request: TripRequest;
  origin_resolved: string;
  origin_coordinates: Coordinates;
  recommendations: DestinationRecommendation[];
  stretch_options: DestinationRecommendation[];
  rejected: RejectedDestination[];
  shortfall: BudgetShortfall | null;
  interest_gap: InterestGap | null;
  ranking_weights: Record<string, number>;
  meta: ResponseMeta;
  confidence: Confidence;
}

export type ActivityKind =
  | 'sightseeing'
  | 'meal'
  | 'transit'
  | 'rest'
  | 'activity'
  | 'check_in'
  | 'check_out'
  | 'free_time';

export interface ItineraryActivity {
  order: number;
  kind: ActivityKind;
  title: string;
  start_time: string;
  end_time: string;
  duration_minutes: number;
  description: string;
  place: PlaceRef | null;
  distance_from_previous_km: number | null;
  transport_note: string | null;
  transport_cost_inr: number;
  entry_cost_inr: number;
  food_cost_inr: number;
  booking_required: boolean;
  weather_alternative: string | null;
  tips: string[];
  block_cost_inr: number;
}

export interface ItineraryDay {
  day_number: number;
  date: string;
  title: string;
  summary: string;
  area_focus: string;
  activities: ItineraryActivity[];
  total_travel_km: number;
  walking_intensity: string;
  weather_note: string | null;
  notes: string[];
  day_cost_inr: number;
}

export interface Itinerary {
  destination: DestinationSummary;
  start_date: string;
  end_date: string;
  travelers: number;
  days: ItineraryDay[];
  cost: CostBreakdown;
  accommodation_area: string;
  accommodation_coordinates: Coordinates | null;
  packing_notes: string[];
  local_tips: string[];
  generated_by: string;
  provenance: Provenance;
  confidence: Confidence;
  disclaimer: string;
  activity_count: number;
}

export interface AppConfig {
  demo_mode: boolean;
  llm: { enabled: boolean; provider: string | null; model: string | null };
  geocoding: string[];
  routing: string[];
  places: string[];
  weather: string[];
  flights: string[];
  hotels: string[];
  maps_browser_key_present: boolean;
  currency: string;
  version: string;
  dataset_version: string;
  destination_count: number;
  cost_model_reference_date: string;
  contingency_rate_default: number;
  stretch_tolerance: number;
  demo_mode_note: string | null;
}

export interface OptionItem {
  value: string;
  label: string;
}

export interface AccommodationOption extends OptionItem {
  priced_per: string;
  sleeps_per_unit: number;
  indicative_inr_per_night: number;
}

export interface PaceOption extends OptionItem {
  activities_per_day: number;
  active_hours_per_day: number;
}

export interface AppOptions {
  travel_modes: OptionItem[];
  accommodation_types: AccommodationOption[];
  paces: PaceOption[];
  interests: OptionItem[];
  dataset_tags: string[];
}

export interface ResolvedPlace {
  coordinates: Coordinates;
  label: string;
  state: string | null;
  country: string | null;
  provenance: Provenance;
  geocoder_configured: boolean;
}

export interface TripSummary {
  id: string;
  title: string;
  destination_id: string;
  destination_name: string;
  budget_inr: number;
  total_estimate_inr: number;
  per_traveler_inr: number;
  travelers: number;
  days: number;
  start_date: string;
  travel_mode: string;
  currency: string;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface TripDetail extends TripSummary {
  request: TripRequest;
  snapshot: Itinerary;
}
