// The /about, /offer and /privacy documents (spec §9b): their structure as catalog keys, and the
// seller's public details from config/legal.yaml (sent in /api/me) as text parameters.

// A document is a lead paragraph and sections; a section is a title and paragraphs (or a list).
// `requisites` adds the seller's details (name, INN, mail, phone) under a section.
export const DOCS = {
  about: {
    title: 'about.title', lead: 'about.lead',
    sections: [
      { title: 'about.what.title', items: ['about.what.1', 'about.what.2'] },
      { title: 'about.free.title', items: ['about.free.1'] },
      { title: 'about.plus.title', items: ['about.plus.1', 'about.plus.2'] },
      { title: 'about.pay.title', items: ['about.pay.1', 'about.pay.2'] },
      { title: 'about.contacts.title', items: ['about.contacts.1'], requisites: true },
    ],
  },
  offer: {
    title: 'offer.title', lead: 'offer.lead',
    sections: [
      { title: 'offer.s1.title', items: ['offer.s1.1', 'offer.s1.2', 'offer.s1.3'] },
      { title: 'offer.s2.title', items: ['offer.s2.1', 'offer.s2.2'] },
      { title: 'offer.s3.title', items: ['offer.s3.1', 'offer.s3.2', 'offer.s3.3', 'offer.s3.4'] },
      { title: 'offer.s4.title', items: ['offer.s4.1', 'offer.s4.2', 'offer.s4.3'] },
      { title: 'offer.s5.title', items: ['offer.s5.1', 'offer.s5.2', 'offer.s5.3'] },
      { title: 'offer.s6.title', items: ['offer.s6.1', 'offer.s6.2'] },
      { title: 'offer.s7.title', items: ['offer.s7.1'] },
      { title: 'offer.s8.title', items: ['offer.s8.1', 'offer.s8.2'] },
      { title: 'legal.req.title', items: [], requisites: true },
    ],
  },
  privacy: {
    title: 'privacy.title', lead: 'privacy.lead',
    sections: [
      { title: 'privacy.s1.title', items: ['privacy.s1.1'] },
      { title: 'privacy.s2.title', items: ['privacy.s2.1', 'privacy.s2.2', 'privacy.s2.3', 'privacy.s2.4'], list: true },
      { title: 'privacy.s3.title', items: ['privacy.s3.1'] },
      { title: 'privacy.s4.title', items: ['privacy.s4.1'] },
      { title: 'privacy.s5.title', items: ['privacy.s5.1', 'privacy.s5.2', 'privacy.s5.3', 'privacy.s5.4', 'privacy.s5.5'], list: true },
      { title: 'privacy.s6.title', items: ['privacy.s6.1', 'privacy.s6.2'] },
      { title: 'privacy.s7.title', items: ['privacy.s7.1', 'privacy.s7.2'] },
      { title: 'privacy.s8.title', items: ['privacy.s8.1'] },
      { title: 'privacy.s9.title', items: ['privacy.s9.1'] },
    ],
  },
};

export const docKeys = doc => [doc.title, doc.lead, ...doc.sections.flatMap(section => [section.title, ...section.items])];

const filled = value => (typeof value === 'string' ? value.trim() : '');

// The seller's details with blanks left empty (the footer hides them).
export function sellerDetails(legal) {
  return {
    name: filled(legal?.seller?.name),
    inn: filled(legal?.seller?.inn),
    email: filled(legal?.email),
    phone: filled(legal?.phone),
  };
}

// Text parameters for the documents: a blank reads as `unset` ("not specified").
export function docParams(legal, unset, site) {
  const details = sellerDetails(legal);
  for (const key of Object.keys(details)) details[key] ||= unset;
  return { ...details, site };
}
