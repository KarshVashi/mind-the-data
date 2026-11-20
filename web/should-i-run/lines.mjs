// Built-in stop order for the Victoria line, from TfL's route sequence
// (latest fetch, 17 Nov 2026). Used to pick stations and to tell which
// board destinations reach the destination until journeys/index.json is
// published. The Victoria line has one route each way, so a train for any
// stop at or after the destination calls there.
const SOUTHBOUND = [
  { naptan_id: "940GZZLUWWL", name: "Walthamstow Central" },
  { naptan_id: "940GZZLUBLR", name: "Blackhorse Road" },
  { naptan_id: "940GZZLUTMH", name: "Tottenham Hale" },
  { naptan_id: "940GZZLUSVS", name: "Seven Sisters" },
  { naptan_id: "940GZZLUFPK", name: "Finsbury Park" },
  { naptan_id: "940GZZLUHAI", name: "Highbury & Islington" },
  { naptan_id: "940GZZLUKSX", name: "King's Cross St. Pancras" },
  { naptan_id: "940GZZLUEUS", name: "Euston" },
  { naptan_id: "940GZZLUWRR", name: "Warren Street" },
  { naptan_id: "940GZZLUOXC", name: "Oxford Circus" },
  { naptan_id: "940GZZLUGPK", name: "Green Park" },
  { naptan_id: "940GZZLUVIC", name: "Victoria" },
  { naptan_id: "940GZZLUPCO", name: "Pimlico" },
  { naptan_id: "940GZZLUVXL", name: "Vauxhall" },
  { naptan_id: "940GZZLUSKW", name: "Stockwell" },
  { naptan_id: "940GZZLUBXN", name: "Brixton" },
];

export const VICTORIA = {
  line: "victoria",
  name: "Victoria",
  directions: {
    // Southbound, Walthamstow Central to Brixton.
    inbound: SOUTHBOUND,
    // Northbound, Brixton to Walthamstow Central.
    outbound: [...SOUTHBOUND].reverse(),
  },
};

export const BUILT_IN_LINES = [VICTORIA];
