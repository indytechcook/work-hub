import {z} from 'zod';

export const pendingStandup=z.object({
 state:z.literal('pending'),date:z.string(),checkin:z.string(),url:z.string(),
 questions:z.array(z.object({id:z.string(),text:z.string(),type:z.string()})).min(1),
});
export const standupDraft=z.object({answers:z.array(z.string().max(4000)).describe('One draft per questionnaire position, with an empty string for unknown answers.')});
export const standupPayload=z.object({form:pendingStandup,draft:standupDraft});
export const standupAnswers=z.array(z.string().trim().min(1).max(4000));
export const standupInstructions='When the weekday standup is pending, pass drafted answers to work_hub_morning_plan when saving. It opens the form and returns its outcome. If the user keeps the existing plan or resumes a saved day with the check-in outstanding, open work_hub_morning_standup with drafted answers in live questionnaire order (CLI: bb work-hub morning standup-review followed by the JSON array of draft strings as one argument). Read the standup phase for prompts. Use the confirmed plan and verified personal accomplishments; leave unknown answers blank for the user. Open the editable form before offering reviews or moving on. The Submit to DailyBot button authorizes only the answers on that form. Report its result; Later or dismissal leaves the check-in pending.';
