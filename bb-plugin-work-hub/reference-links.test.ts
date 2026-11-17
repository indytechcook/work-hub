import {test} from 'node:test';
import assert from 'node:assert/strict';
import {referenceLinks} from './reference-links.ts';
const pr={title:'Fix reviews',url:'https://github.com/example/app/pull/12'};
const issue={title:'Ticket',url:'https://linear.app/example/issue/APP-42/ticket'};
const links=referenceLinks([pr,issue],[pr]);
test('resolves source titles, PRs and Linear tickets in prose',()=>{
 assert.equal(links.titleUrl('Fix reviews'),pr.url);
 assert.deepEqual(links.parts('Review PR #12, APP-42 and APP-43.'),[
  {text:'Review '},{text:'PR #12',href:pr.url},{text:', '},{text:'APP-42',href:issue.url},
  {text:' and '},{text:'APP-43',href:'https://linear.app/example/issue/APP-43'},{text:'.'}]);
});
test('preserves URL punctuation and does not link inside an existing URL',()=>{
 const text=`See (${pr.url}), then APP-42.`;
 const parts=links.parts(text);
 assert.equal(parts.map(p=>p.text).join(''),text);
 assert.deepEqual(parts.filter(p=>p.href).map(p=>p.href),[pr.url,issue.url]);
});
test('ambiguous PRs and unknown identifiers stay plain; local repo disambiguates',()=>{
 const other={title:'Other',url:'https://github.com/example/other/pull/12'};
 assert.deepEqual(referenceLinks([pr,other]).parts('PR #12'),[{text:'PR #12'}]);
 assert.equal(referenceLinks([pr,other],[pr]).parts('#12')[0].href,pr.url);
 assert.deepEqual(links.parts('UNKNOWN-9 and #999'),[{text:'UNKNOWN-9 and #999'}]);
 assert.equal(links.parts('example/other#12')[0].href,other.url);
});
test('does not turn unsafe source URLs into title links',()=>{
 const bad={title:'Bad',url:'javascript:alert(1)'};
 assert.equal(referenceLinks([bad],[bad]).titleUrl('Bad'),undefined);
 assert.deepEqual(links.parts('javascript:alert(1)'),[{text:'javascript:alert(1)'}]);
});
