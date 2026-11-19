import {z} from 'zod';

export const morningQuestion = z.object({
 question:z.string().trim().min(1).max(350),
 choices:z.array(z.string().trim().min(1).max(200)).min(2).max(5)
  .refine(choices=>new Set(choices).size===choices.length,'Offer distinct choices'),
});

export const decisionInstructions = 'For each planning decision, ask one question at a time using work_hub_morning_question. For CLI-only sessions use bb work-hub morning ask "<question>" "<choice 1>" "<choice 2>" [more choices]. Offer concrete choices with the recommended option first. For plan confirmation, show the proposed order before offering Save this plan, Adjust priorities, or Keep current plan. Use another choice card for each adjustment. Wait for an actual selection. Dismissal, expiry, or a tool error leaves the current plan in place. Do not ask for a typed reply or treat a waiting notice as an answer.';
